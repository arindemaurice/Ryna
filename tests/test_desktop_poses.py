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
        self.assertGreater(dict(w_morning)["walk"], 0.5)

        period_afternoon, w_afternoon = time_of_day_weights(14)
        self.assertEqual(period_afternoon, "AFTERNOON")
        self.assertIn("sit", dict(w_afternoon))
        self.assertIn("walk", dict(w_afternoon))

        period_evening, w_evening = time_of_day_weights(20)
        self.assertEqual(period_evening, "EVENING")
        self.assertGreater(dict(w_evening)["sit"], 0.4)

        rng = random.Random(42)
        night_actions = [choose_timed_action(2, walk_enabled=True, rng=rng)[0] for _ in range(30)]
        self.assertIn("sleep", night_actions)
        morning_actions = [choose_timed_action(9, walk_enabled=True, rng=rng)[0] for _ in range(30)]
        self.assertIn("walk", morning_actions)

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
            finally:
                if app is not None:
                    app.quit_app()
                core.close()


if __name__ == "__main__":
    unittest.main()
