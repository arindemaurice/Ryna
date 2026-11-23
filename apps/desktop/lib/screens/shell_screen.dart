import 'package:flutter/material.dart';
import '../models/protocol.dart';
import '../services/ryna_api_client.dart';
import '../widgets/companion_orb.dart';

class RynaShellScreen extends StatefulWidget {
  const RynaShellScreen({super.key});

  @override
  State<RynaShellScreen> createState() => _RynaShellScreenState();
}

class _RynaShellScreenState extends State<RynaShellScreen> {
  final RynaApiClient _api = RynaApiClient();
  final TextEditingController _chatCtrl = TextEditingController();
  int _selectedIndex = 0;
  RynaStatus? _status;
  String? _error;
  String? _convId;
  List<RynaMessage> _messages = [];
  List<RynaTask> _tasks = [];
  List<RynaMemory> _memories = [];

  @override
  void initState() {
    super.initState();
    _refresh();
  }

  Future<void> _refresh() async {
    try {
      final st = await _api.fetchStatus();
      final tasks = await _api.fetchTasks();
      final mems = await _api.fetchMemories();
      setState(() {
        _status = st;
        _tasks = tasks;
        _memories = mems;
        _error = null;
      });
    } catch (e) {
      setState(() => _error = 'Local RYNA engine unreachable at ${_api.baseUrl}: $e');
    }
  }

  Future<void> _send() async {
    final txt = _chatCtrl.text.trim();
    if (txt.isEmpty) return;
    _chatCtrl.clear();
    try {
      final res = await _api.sendChat(_convId, txt);
      _convId = res['conversation_id']?.toString();
      if (_convId != null) {
        final msgs = await _api.fetchMessages(_convId!);
        setState(() => _messages = msgs);
      }
      await _refresh();
    } catch (e) {
      setState(() => _error = e.toString());
    }
  }

  @override
  Widget build(BuildContext context) {
    final destinations = const [
      NavigationRailDestination(icon: Icon(Icons.chat_bubble_outline), label: Text('Assistant')),
      NavigationRailDestination(icon: Icon(Icons.blur_circular), label: Text('Companion')),
      NavigationRailDestination(icon: Icon(Icons.task_alt), label: Text('Tasks')),
      NavigationRailDestination(icon: Icon(Icons.memory), label: Text('Memory')),
      NavigationRailDestination(icon: Icon(Icons.settings), label: Text('Settings')),
    ];

    return Scaffold(
      body: Row(
        children: [
          NavigationRail(
            selectedIndex: _selectedIndex,
            onDestinationSelected: (i) => setState(() => _selectedIndex = i),
            labelType: NavigationRailLabelType.all,
            destinations: destinations,
          ),
          const VerticalDivider(width: 1),
          Expanded(
            child: Column(
              children: [
                if (_error != null)
                  Container(
                    width: double.infinity,
                    color: Colors.red.withOpacity(0.2),
                    padding: const EdgeInsets.all(10),
                    child: Text(_error!, style: const TextStyle(color: Colors.redAccent)),
                  ),
                Expanded(child: _buildBody()),
              ],
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildBody() {
    switch (_selectedIndex) {
      case 0:
        return Column(
          children: [
            Expanded(
              child: ListView.builder(
                padding: const EdgeInsets.all(16),
                itemCount: _messages.length,
                itemBuilder: (_, i) {
                  final m = _messages[i];
                  return Card(
                    child: ListTile(
                      title: Text(m.content),
                      subtitle: Text('${m.role.toUpperCase()} • ${m.provider ?? 'system'} • ${m.mode}'),
                    ),
                  );
                },
              ),
            ),
            Padding(
              padding: const EdgeInsets.all(12),
              child: Row(
                children: [
                  Expanded(
                    child: TextField(
                      controller: _chatCtrl,
                      decoration: const InputDecoration(hintText: 'Message RYNA or run /diag, /calc...'),
                      onSubmitted: (_) => _send(),
                    ),
                  ),
                  const SizedBox(width: 8),
                  FilledButton(onPressed: _send, child: const Text('Send')),
                ],
              ),
            ),
          ],
        );
      case 1:
        const states = ['idle', 'listening', 'thinking', 'speaking', 'working', 'waiting', 'alert', 'sleeping'];
        return Center(
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              CompanionOrb(state: _status?.companionState ?? 'idle', size: 220),
              const SizedBox(height: 16),
              Wrap(
                spacing: 8,
                children: states
                    .map((s) => OutlinedButton(
                          onPressed: () async {
                            await _api.setCompanionState(s);
                            await _refresh();
                          },
                          child: Text(s),
                        ))
                    .toList(),
              ),
            ],
          ),
        );
      case 2:
        return ListView(
          padding: const EdgeInsets.all(16),
          children: _tasks
              .map((t) => Card(
                    child: ListTile(
                      title: Text(t.title),
                      subtitle: Text('${t.goal}\nStatus: ${t.status} (${t.outcomeStatus}) • L${t.permissionLevel}'),
                    ),
                  ))
              .toList(),
        );
      case 3:
        return ListView(
          padding: const EdgeInsets.all(16),
          children: _memories
              .map((m) => Card(
                    child: ListTile(
                      title: Text(m.content),
                      subtitle: Text('${m.category} • ${m.memoryType} • ${(m.confidence * 100).round()}%'),
                    ),
                  ))
              .toList(),
        );
      default:
        return Center(
          child: Padding(
            padding: const EdgeInsets.all(24),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                Text('RYNA ${_status?.version ?? '0.1.0'}', style: Theme.of(context).textTheme.headlineSmall),
                const SizedBox(height: 8),
                Text('Mode: ${_status?.online == true ? 'Online' : 'Offline'} | Tier: ${_status?.recommendedTier ?? '?'}'),
                const SizedBox(height: 16),
                FilledButton(onPressed: _refresh, child: const Text('Refresh Engine Status')),
              ],
            ),
          ),
        );
    }
  }
}
