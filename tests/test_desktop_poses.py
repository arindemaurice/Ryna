"""Tests for RYNA's multi-pose sprite set, time-of-day scheduler, dictation display, and voice pose commands."""

from __future__ import annotations

from pathlib import Path
import random
import tempfile
import unittest

import numpy as np
from PIL import Image

from ai.config import RynaConfig
from ai.orchestration.core import RynaCore
from app.desktop.robot_window import RynaRobotDesktop, choose_timed_action, time_of_day_weights


class TestDesktopPosesAndSchedule(unittest.TestCase):
    def setUp(self) -> None:
        self.project_root = Path(__file__).resolve().parents[1]
        self.assets_dir = self.project_root / "app" / "desktop" / "assets"

    def test_all_pose_assets_exist_and_legs_interchange(self) -> None:
        required = (
            "ryna_robot.png",
            "ryna_walk_a.png",
            "ryna_walk_mid.png",
            "ryna_walk_b.png",
            "ryna_stand.png",
            "ryna_think.png",
            "ryna_sit.png",
            "ryna_sleep.png",
        )
        for name in required:
            p = self.assets_dir / name
            self.assertTrue(p.is_file(), f"Missing pose asset: {p}")
            with Image.open(p) as im:
                self.assertEqual(im.mode, "RGBA")
                self.assertGreaterEqual(im.size[0], 300)
                self.assertGreaterEqual(im.size[1], 400)

        with Image.open(self.assets_dir / "ryna_walk_a.png") as ima, Image.open(self.assets_dir / "ryna_walk_b.png") as imb:
            a = np.array(ima.convert("RGBA"))[330:, :, 3] > 20
            b = np.array(imb.convert("RGBA"))[330:, :, 3] > 20
        diff_ratio = float(np.mean(a != b))
        self.assertGreater(diff_ratio, 0.08, "walk_a and walk_b leg pixels must visibly interchange.")

    def test_time_of_day_scheduler_profiles(self) -> None:
        period_night, w_night = time_of_day_weights(23)
        self.assertEqual(period_night, "NIGHT")
        self.assertGreater(dict(w_night)["sleep"], 0.7)

        period_morning, w_morning = time_of_day_weights(9)
        self.assertEqual(period_morning, "MORNING")
        self.assertGreater(dict(w_morning)["sit"], 0.55)

        period_afternoon, w_afternoon = time_of_day_weights(14)
        self.assertEqual(period_afternoon, "AFTERNOON")
        self.assertGreater(dict(w_afternoon)["sit"], 0.6)
        self.assertIn("walk", dict(w_afternoon))

        period_evening, w_evening = time_of_day_weights(20)
        self.assertEqual(period_evening, "EVENING")
        self.assertGreater(dict(w_evening)["sit"], 0.6)

        rng = random.Random(42)
        night_actions = [choose_timed_action(2, walk_enabled=True, rng=rng)[0] for _ in range(30)]
        self.assertIn("sleep", night_actions)
        morning_actions = [choose_timed_action(9, walk_enabled=True, rng=rng)[0] for _ in range(30)]
        self.assertGreater(morning_actions.count("sit"), morning_actions.count("walk"))

    def test_robot_window_poses_dictation_and_voice_commands(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            cfg = RynaConfig.load(
                overrides={
                    "project_root": self.project_root,
                    "data_dir": base,
                    "db_path": base / "ryna.db",
                    "log_dir": base / "logs",
                    "backup_dir": base / "backups",
                }
            )
            core = RynaCore(cfg)
            app = None
            try:
                app = RynaRobotDesktop(core)
                self.assertEqual(len(app._walk_right), 4)
                self.assertEqual(len(app._walk_left), 4)
                self.assertIsNotNone(app._pose_stand)
                self.assertIsNotNone(app._pose_think)
                self.assertIsNotNone(app._pose_sit)
                self.assertIsNotNone(app._pose_sleep)

                # Verify dictation text appears clearly in the text box above RYNA
                app._show_bubble("Thinking…", dictation_text="What time is it", show_mic_bar=False)
                app.root.update_idletasks()
                self.assertEqual(app._dictation_var.get(), "What time is it")
                self.assertTrue(app._dict_container.winfo_ismapped())

                # Verify live listening bar appears while listening
                app._show_bubble("Speak whenever you're ready.", dictation_text="", show_mic_bar=True)
                app.root.update_idletasks()
                self.assertTrue(app._mic_bar.winfo_ismapped())

                # Verify voice-controlled pose commands
                self.assertIsNotNone(app._try_voice_setting("sit down"))
                self.assertEqual(app.current_action, "sit")
                self.assertEqual(app.manual_pose, "sit")

                self.assertIsNotNone(app._try_voice_setting("go to sleep"))
                self.assertEqual(app.current_action, "sleep")
                self.assertEqual(app.manual_pose, "sleep")

                self.assertIsNotNone(app._try_voice_setting("stand up"))
                self.assertEqual(app.current_action, "stand")

                self.assertIsNotNone(app._try_voice_setting("wake up"))
                self.assertIsNone(app.manual_pose)

                # Verify right-click Monitor & Settings panel opens, refreshes live telemetry, and toggles settings
                app.open_settings_panel()
                panel = app._settings_panel
                self.assertIsNotNone(panel)
                panel.win.update_idletasks()
                self.assertIn("Pose:", panel._mon_vars["state"].get())
                panel._set_pose("sit")
                self.assertEqual(app.current_action, "sit")
                panel._var_phys_search.set(False)
                core.db.set_setting("desktop.physical_app_search", False)
                self.assertFalse(bool(core.db.get_setting("desktop.physical_app_search", True)))

                # Verify avatar customization: theme buttons, hue shifts, and voice color commands
                panel._apply_color_theme(40, "#ffab00")
                self.assertEqual(app.chassis_hue_shift, 40)
                self.assertEqual(app.accent_color, "#ffab00")
                self.assertEqual(core.db.get_setting("desktop.chassis_hue_shift"), 40)
                self.assertEqual(core.db.get_setting("desktop.accent_color"), "#ffab00")

                res_voice = app._try_voice_setting("change color to blue")
                self.assertIsNotNone(res_voice)
                self.assertIn("blue", str(res_voice).lower())
                self.assertEqual(app.chassis_hue_shift, 220)
                self.assertEqual(app.accent_color, "#2979ff")

                panel.close()

                # Verify physical app launcher hook routes "open word" cleanly
                launched: list[str] = []
                core.db.set_setting("desktop.physical_app_search", True)
                core.physical_app_launcher = lambda name: (launched.append(name) or {"launched": True, "app": "Word", "method": "windows_search_bar"})
                res = core.send_chat(None, "please open word")
                self.assertEqual(launched, ["word"])
                self.assertIn("Opening Word", res["assistant_message"]["content"])

                # Verify sentence guard does NOT hijack normal conversation as an app launch
                launched.clear()
                m_bat = core.send_chat(None, "how much battery do i have")
                self.assertEqual(launched, [])
                self.assertIn("battery", m_bat["assistant_message"]["content"].lower())
            finally:
                if app is not None:
                    app.quit_app()
                core.close()

    def test_ryna_app_registry_and_aliases(self) -> None:
        from computer.controller import resolve_app_spec

        k_word, s_word = resolve_app_spec("Microsoft Word")
        self.assertEqual(k_word, "word")
        self.assertIsNotNone(s_word)
        assert s_word is not None
        self.assertEqual(s_word["name"], "Word")

        k_code, _ = resolve_app_spec("vs code")
        self.assertIn(k_code, ("vscode", "vs code"))

        k_pc, s_pc = resolve_app_spec("my computer")
        self.assertEqual(k_pc, "thispc")
        assert s_pc is not None
        self.assertEqual(s_pc["folder"], "thispc")

    def test_ryna_system_manager_files_power_doctor_and_reminders(self) -> None:
        from datetime import datetime, timedelta, timezone
        from computer.system_manager import RynaSystemManager

        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            home = base / "home"
            (home / "Documents").mkdir(parents=True)
            mgr = RynaSystemManager(self.project_root, home_dir=home)

            # Safe file write/read/copy/move/recycle inside user profile
            w = mgr.write_user_file("Documents/hello.txt", "RYNA system test")
            self.assertTrue(w["written"])
            r = mgr.read_user_file("Documents/hello.txt")
            self.assertEqual(r["content"], "RYNA system test")

            # Executable/script write blocked
            with self.assertRaises(PermissionError):
                mgr.write_user_file("Documents/bad.ps1", "Write-Host hi")

            # Escape outside user profile blocked
            with self.assertRaises(PermissionError):
                mgr.resolve_user_path("C:/Windows/System32/cmd.exe")

            # Two-step power confirmation + cancel
            req = mgr.request_power_action("shutdown")
            self.assertTrue(req["needs_confirmation"])
            can = mgr.cancel_power_action()
            self.assertTrue(can["cancelled"])
            with self.assertRaises(ValueError):
                mgr.confirm_power_action("shutdown")

            # Live telemetry & RYNA Tech Doctor (always RYNA, zero Jarvis/Ultron branding)
            tel = mgr.sample_telemetry()
            self.assertIn(tel["health"], ("HEALTHY", "WARNING"))
            doc = mgr.diagnose_issues("Ollama connection refused on 11434")
            self.assertTrue(doc["findings"])
            self.assertNotIn("jarvis", doc["summary"].lower())
            self.assertNotIn("ultron", doc["summary"].lower())

            # Due reminder check in RynaCore SQLite
            cfg = RynaConfig.load(
                overrides={
                    "project_root": self.project_root,
                    "data_dir": base / "data",
                    "db_path": base / "data" / "ryna.db",
                    "log_dir": base / "data" / "logs",
                    "backup_dir": base / "data" / "backups",
                }
            )
            core = RynaCore(cfg)
            try:
                past = (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat()
                core.db.create_note("Stretch", "Time to stretch!", remind_at=past)
                due = core.check_due_reminders()
                self.assertEqual(len(due), 1)
                self.assertEqual(due[0]["title"], "Stretch")
                # Second check should not fire twice
                self.assertEqual(core.check_due_reminders(), [])
            finally:
                core.close()

    def test_autostart_launcher_lifecycle(self) -> None:
        from app.desktop.autostart import disable_autostart, enable_autostart, find_pythonw, is_autostart_enabled

        with tempfile.TemporaryDirectory() as td:
            fake_startup = Path(td)
            self.assertFalse(is_autostart_enabled(fake_startup))
            vbs = enable_autostart(self.project_root, custom_startup_dir=fake_startup)
            self.assertTrue(vbs.is_file())
            self.assertTrue(is_autostart_enabled(fake_startup))
            txt = vbs.read_text(encoding="utf-8")
            self.assertIn("ai.orchestration.main", txt)
            self.assertIn("pythonw", txt.lower())
            self.assertTrue(disable_autostart(fake_startup))
            self.assertFalse(is_autostart_enabled(fake_startup))
            self.assertTrue(find_pythonw().lower().endswith(("pythonw.exe", "python.exe")))

    def test_no_tick_timer_leak_original_theme_and_phone_bridge(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            cfg = RynaConfig.load(
                overrides={
                    "project_root": self.project_root,
                    "data_dir": base,
                    "db_path": base / "ryna.db",
                    "log_dir": base / "logs",
                    "backup_dir": base / "backups",
                }
            )
            core = RynaCore(cfg)
            # Pre-seed 'original' accent color to ensure Tk never throws TclError on startup
            core.db.set_setting("desktop.accent_color", "original")
            app = None
            try:
                app = RynaRobotDesktop(core)
                for _ in range(25):
                    app._tick()
                    app._redraw_now()
                after_ids = app.root.tk.call("after", "info")
                self.assertLessEqual(len(after_ids), 3, f"Timer leak detected: {len(after_ids)} pending callbacks!")

                if app._phone_bridge is not None:
                    png = app._phone_bridge.render_avatar_png()
                    self.assertTrue(png.startswith(b"\x89PNG"))
            finally:
                if app is not None:
                    app.quit_app()
                core.close()


if __name__ == "__main__":
    unittest.main()
