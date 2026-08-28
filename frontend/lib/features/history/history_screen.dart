import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';
import 'package:provider/provider.dart';
import '../../core/state/connection_controller.dart';
import '../../core/widgets/review_layout.dart';

class HistoryScreen extends StatefulWidget {
  const HistoryScreen({super.key, required this.projectId});
  final String projectId;
  @override
  State<HistoryScreen> createState() => _HistoryScreenState();
}

class _HistoryScreenState extends State<HistoryScreen> {
  List<Map<String, dynamic>> _plans = [], _patches = [];
  bool _busy = true;
  String? _error;
  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      final api = context.read<ConnectionController>().api;
      final plans = await api.listPlans(widget.projectId);
      final patches = await api.listPatches(widget.projectId);
      if (mounted) {
        setState(() {
          _plans = plans;
          _patches = patches;
        });
      }
    } catch (e) {
      if (mounted) setState(() => _error = e.toString());
    }
    if (mounted) setState(() => _busy = false);
  }

  @override
  Widget build(BuildContext context) => ReviewLayout(
    title: 'PLAN & PATCH HISTORY',
    busy: _busy,
    error: _error,
    onRefresh: _load,
    children: [
      const Section(
        title: 'RESUME SAFELY',
        child: Text(
          'Open a stored plan or patch to load its current approval and revision state. Refresh here after an AI request times out before generating anything again.',
        ),
      ),
      Section(
        title: 'PLANS',
        child: Column(
          children: [
            if (_plans.isEmpty && !_busy) const Text('No plans recorded.'),
            for (final p in _plans)
              ListTile(
                title: Text('${p['plan_id']}'),
                subtitle: Text(
                  '${p['request']}',
                  maxLines: 3,
                  overflow: TextOverflow.ellipsis,
                ),
                trailing: const Icon(Icons.chevron_right),
                onTap: () => context.push(
                  '/project/${widget.projectId}/plan/${p['plan_id']}',
                ),
              ),
          ],
        ),
      ),
      Section(
        title: 'PATCHES',
        child: Column(
          children: [
            if (_patches.isEmpty && !_busy) const Text('No patches recorded.'),
            for (final p in _patches)
              ListTile(
                title: Text('${p['patch_id']}'),
                subtitle: Text(
                  'Plan ${p['plan_id']} · revision ${p['workspace_revision']} · ${p['operation_count']} operations',
                ),
                trailing: const Icon(Icons.chevron_right),
                onTap: () => context.push(
                  '/project/${widget.projectId}/patch/${p['patch_id']}',
                ),
              ),
          ],
        ),
      ),
    ],
  );
}
