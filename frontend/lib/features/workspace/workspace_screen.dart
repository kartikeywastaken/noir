import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';
import 'package:provider/provider.dart';
import '../../core/state/workspace_controller.dart';
import '../../core/theme/noir_typography.dart';
import '../../core/widgets/code_viewer.dart';
import '../../core/widgets/noir_app_bar.dart';
import '../../core/widgets/noir_button.dart';
import '../../core/widgets/review_layout.dart';
import '../../data/models/models.dart';

class WorkspaceScreen extends StatefulWidget {
  const WorkspaceScreen({super.key, required this.projectId});
  final String projectId;
  @override
  State<WorkspaceScreen> createState() => _WorkspaceScreenState();
}

class _WorkspaceScreenState extends State<WorkspaceScreen> {
  final _editor = TextEditingController();
  bool _generating = false;
  WorkspaceController get ws => context.read<WorkspaceController>();
  @override
  void initState() {
    super.initState();
    Future.microtask(() {
      if (mounted) ws.initialize(widget.projectId);
    });
  }

  @override
  void dispose() {
    _editor.dispose();
    super.dispose();
  }

  Future<bool> _canLeave() async {
    if (!ws.dirty) return true;
    final discard = await confirmAction(
      context,
      'Unsaved editor changes',
      'Discard only this unsaved buffer? Files already saved to the backend remain changed and must still be recorded.',
      'Discard buffer',
    );
    if (discard && mounted) {
      ws.discardDraft();
      _editor.text = ws.draft ?? '';
      await WidgetsBinding.instance.endOfFrame;
    }
    return discard;
  }

  Future<void> _navigate(String route) async {
    if (!await _canLeave() || !mounted) return;
    await context.push('/project/${widget.projectId}/$route');
    if (mounted) await _refreshWorkspace();
  }

  Future<void> _refreshWorkspace() async {
    await ws.refresh();
    if (mounted && !ws.dirty && ws.selectedFilePath != null) {
      await _open(ws.selectedFilePath!);
    }
  }

  Future<void> _beginManual() async {
    await ws.beginManual();
    if (mounted &&
        ws.manualActive &&
        ws.selectedFilePath != null &&
        !ws.dirty) {
      await _open(ws.selectedFilePath!);
    }
  }

  Future<void> _open(String path) async {
    if (!await _canLeave() || !mounted) return;
    await ws.openFile(path);
    if (mounted) _editor.text = ws.draft ?? '';
  }

  @override
  Widget build(BuildContext context) => Consumer<WorkspaceController>(
    builder: (context, state, _) => PopScope(
      canPop: !state.dirty && !state.busy && !_generating,
      onPopInvokedWithResult: (didPop, result) async {
        if (!didPop &&
            !state.busy &&
            !_generating &&
            await _canLeave() &&
            context.mounted) {
          if (context.canPop()) {
            context.pop();
          } else {
            context.go('/');
          }
        }
      },
      child: Scaffold(
        appBar: NoirAppBar(
          title: state.project?.packageName ?? 'WORKSPACE',
          showBackButton: true,
          actions: [
            IconButton(
              tooltip: 'Static inventory',
              onPressed: _inventory,
              icon: const Icon(Icons.info_outline),
            ),
            IconButton(
              tooltip: 'Refresh workspace',
              onPressed: state.busy ? null : _refreshWorkspace,
              icon: const Icon(Icons.refresh),
            ),
          ],
        ),
        body: Column(
          children: [
            SingleChildScrollView(
              scrollDirection: Axis.horizontal,
              padding: const EdgeInsets.all(12),
              child: Row(
                children: [
                  NoirGhostButton(
                    label: 'Ask AI',
                    icon: Icons.auto_awesome,
                    onPressed: state.manualActive || state.busy || _generating
                        ? null
                        : _askAi,
                  ),
                  const SizedBox(width: 8),
                  NoirGhostButton(
                    label: state.manualActive ? 'Manual active' : 'Manual edit',
                    icon: Icons.edit_note,
                    loading: state.busy,
                    onPressed: state.manualActive ? null : _beginManual,
                  ),
                  for (final route in [
                    'history',
                    'build',
                    'sign',
                    'audit',
                  ]) ...[
                    const SizedBox(width: 8),
                    NoirGhostButton(
                      label: route,
                      onPressed: state.busy || _generating
                          ? null
                          : () => _navigate(route),
                    ),
                  ],
                ],
              ),
            ),
            if (state.manualActive)
              Padding(
                padding: const EdgeInsets.symmetric(horizontal: 12),
                child: Section(
                  title:
                      'MANUAL SESSION · REVISION ${state.project?.workspaceRevision}',
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      const Text(
                        'Save writes to the backend. Record finalizes those changes and invalidates old approvals. Leaving does not roll back saved files.',
                      ),
                      const SizedBox(height: 10),
                      NoirPrimaryButton(
                        label: 'Record changes',
                        loading: state.busy,
                        onPressed: state.dirty
                            ? null
                            : () async {
                                final ok = await state.record();
                                if (ok && context.mounted) {
                                  ScaffoldMessenger.of(context).showSnackBar(
                                    const SnackBar(
                                      content: Text('Manual session recorded.'),
                                    ),
                                  );
                                }
                              },
                      ),
                    ],
                  ),
                ),
              ),
            if (state.error != null)
              Padding(
                padding: const EdgeInsets.all(12),
                child: SelectableText(state.error!),
              ),
            Expanded(
              child: LayoutBuilder(
                builder: (context, box) => box.maxWidth >= 800
                    ? Row(
                        children: [
                          SizedBox(width: 280, child: _tree(state)),
                          const VerticalDivider(width: 1),
                          Expanded(child: _code(state)),
                        ],
                      )
                    : state.selectedFilePath == null
                    ? _tree(state)
                    : _code(state),
              ),
            ),
          ],
        ),
        floatingActionButton: state.manualActive
            ? null
            : FloatingActionButton.small(
                tooltip: 'Ask AI',
                onPressed: _generating || state.busy ? null : _askAi,
                child: const Icon(Icons.auto_awesome),
              ),
      ),
    ),
  );

  Widget _tree(WorkspaceController state) => Column(
    children: [
      Row(
        children: [
          IconButton(
            tooltip: 'Parent folder',
            onPressed: state.currentSubdir.isEmpty
                ? null
                : () {
                    final parts = state.currentSubdir.split('/')..removeLast();
                    state.loadFiles(subdir: parts.join('/'));
                  },
            icon: const Icon(Icons.arrow_upward),
          ),
          Expanded(
            child: Text(
              '/${state.currentSubdir}',
              overflow: TextOverflow.ellipsis,
              style: NoirTypography.codeSm,
            ),
          ),
          IconButton(
            tooltip: 'Search file contents',
            onPressed: _search,
            icon: const Icon(Icons.search),
          ),
        ],
      ),
      if (state.loadingFiles) const LinearProgressIndicator(),
      Expanded(
        child: ListView.builder(
          itemCount: state.files.length,
          itemBuilder: (_, i) {
            final file = state.files[i];
            return ListTile(
              dense: true,
              leading: Icon(
                file.isDirectory
                    ? Icons.folder_outlined
                    : Icons.description_outlined,
                size: 18,
              ),
              title: Text(file.name, style: NoirTypography.codeSm),
              selected: file.path == state.selectedFilePath,
              onTap: state.busy
                  ? null
                  : () => file.isDirectory
                        ? state.loadFiles(subdir: file.path)
                        : _open(file.path),
            );
          },
        ),
      ),
    ],
  );

  Widget _code(WorkspaceController state) {
    if (state.selectedFilePath == null) {
      return const Center(
        child: Text('Select decoded resources, manifest or Smali.'),
      );
    }
    return Column(
      children: [
        Row(
          children: [
            IconButton(
              tooltip: 'Close file',
              onPressed: () async {
                if (await _canLeave() && mounted) state.closeFile();
              },
              icon: const Icon(Icons.close),
            ),
            Expanded(
              child: Text(
                '${state.selectedFilePath}${state.dirty ? ' • unsaved' : ''}',
                style: NoirTypography.codeSm,
                overflow: TextOverflow.ellipsis,
              ),
            ),
            if (state.editable) ...[
              IconButton(
                tooltip: 'Discard unsaved buffer',
                onPressed: state.dirty && !state.busy
                    ? () async {
                        await _canLeave();
                      }
                    : null,
                icon: const Icon(Icons.undo),
              ),
              IconButton(
                tooltip: 'Save to backend',
                onPressed:
                    state.dirty && !state.busy && state.fileRevision != null
                    ? state.saveFile
                    : null,
                icon: const Icon(Icons.save_outlined),
              ),
            ],
          ],
        ),
        if (state.loadingContent) const LinearProgressIndicator(),
        Expanded(
          child: state.editable && state.fileRevision != null
              ? Padding(
                  padding: const EdgeInsets.all(12),
                  child: TextField(
                    controller: _editor,
                    onChanged: state.edit,
                    readOnly: state.busy,
                    keyboardType: TextInputType.multiline,
                    maxLines: null,
                    expands: true,
                    autocorrect: false,
                    enableSuggestions: false,
                    style: NoirTypography.codeSm,
                    decoration: const InputDecoration(border: InputBorder.none),
                  ),
                )
              : CodeViewer(
                  content: state.fileContent ?? 'Unable to load this file.',
                ),
        ),
      ],
    );
  }

  Future<void> _askAi() async {
    if (!await _canLeave() || !mounted) return;
    final request = TextEditingController();
    var consent = false;
    var running = false;
    String? error;
    final plan = await showDialog<ChangePlan>(
      context: context,
      barrierDismissible: false,
      builder: (dialogContext) => StatefulBuilder(
        builder: (dialogContext, update) => PopScope(
          canPop: !running,
          child: AlertDialog(
            title: const Text('Ask AI'),
            content: SizedBox(
              width: 520,
              child: SingleChildScrollView(
                child: Column(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    TextField(
                      controller: request,
                      maxLines: 5,
                      readOnly: running,
                      onChanged: (_) => update(() {}),
                      decoration: const InputDecoration(
                        hintText: 'Describe the exact change.',
                      ),
                    ),
                    CheckboxListTile(
                      value: consent,
                      onChanged: running
                          ? null
                          : (v) => update(() => consent = v ?? false),
                      title: const Text(
                        'I consent to sending selected workspace context and this request to the backend AI provider.',
                      ),
                    ),
                    const Text(
                      'No change is applied here. Review and approve the exact plan, then review and approve its patch.',
                    ),
                    if (running)
                      const Padding(
                        padding: EdgeInsets.all(12),
                        child: LinearProgressIndicator(),
                      ),
                    if (error != null) SelectableText(error!),
                  ],
                ),
              ),
            ),
            actions: [
              TextButton(
                onPressed: running ? null : () => Navigator.pop(dialogContext),
                child: const Text('Close'),
              ),
              FilledButton(
                onPressed: running || !consent || request.text.trim().isEmpty
                    ? null
                    : () async {
                        update(() {
                          running = true;
                          error = null;
                        });
                        setState(() => _generating = true);
                        try {
                          final result = await ws.api.createPlan(
                            widget.projectId,
                            request.text.trim(),
                            allowAiUpload: consent,
                          );
                          if (dialogContext.mounted) {
                            Navigator.pop(dialogContext, result);
                          }
                        } catch (e) {
                          if (dialogContext.mounted) {
                            update(() {
                              error = e.toString();
                              running = false;
                            });
                          }
                        } finally {
                          if (mounted) setState(() => _generating = false);
                        }
                      },
                child: const Text('Generate plan'),
              ),
            ],
          ),
        ),
      ),
    );
    request.dispose();
    if (plan != null && mounted) await _navigate('plan/${plan.planId}');
  }

  Future<void> _search() async {
    final query = TextEditingController();
    var results = <Map<String, dynamic>>[];
    var loading = false;
    String? error;
    final path = await showDialog<String>(
      context: context,
      builder: (dialogContext) => StatefulBuilder(
        builder: (dialogContext, update) => AlertDialog(
          title: const Text('Search decoded text'),
          content: SizedBox(
            width: 600,
            height: 400,
            child: Column(
              children: [
                TextField(
                  controller: query,
                  decoration: const InputDecoration(
                    hintText: 'Text to find (up to 100 results)',
                  ),
                  onSubmitted: (value) async {
                    if (loading || value.trim().isEmpty) return;
                    update(() {
                      loading = true;
                      error = null;
                    });
                    try {
                      final found = await ws.api.searchFiles(
                        widget.projectId,
                        value,
                      );
                      if (dialogContext.mounted) update(() => results = found);
                    } catch (e) {
                      if (dialogContext.mounted) {
                        update(() => error = e.toString());
                      }
                    }
                    if (dialogContext.mounted) update(() => loading = false);
                  },
                ),
                if (loading) const LinearProgressIndicator(),
                if (error != null) Text(error!),
                Expanded(
                  child: ListView(
                    children: results
                        .map(
                          (r) => ListTile(
                            title: Text(
                              '${r['file']}:${r['line']}',
                              style: NoirTypography.codeSm,
                            ),
                            subtitle: Text(
                              '${r['content']}',
                              maxLines: 2,
                              overflow: TextOverflow.ellipsis,
                            ),
                            onTap: () => Navigator.pop(
                              dialogContext,
                              r['file'] as String,
                            ),
                          ),
                        )
                        .toList(),
                  ),
                ),
              ],
            ),
          ),
          actions: [
            TextButton(
              onPressed: () => Navigator.pop(dialogContext),
              child: const Text('Close'),
            ),
          ],
        ),
      ),
    );
    query.dispose();
    if (path != null && mounted) await _open(path);
  }

  void _inventory() {
    final analysis = ws.analysis;
    showDialog<void>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('Static inventory'),
        content: SingleChildScrollView(
          child: SelectableText(
            analysis == null
                ? 'Analysis unavailable. Refresh the workspace.'
                : 'Package: ${analysis.packageName}\nMinimum SDK: ${analysis.minSdk}\nTarget SDK: ${analysis.targetSdk}\nMultidex: ${analysis.multidex}\n\nPermissions\n${analysis.permissions.join('\n')}\n\nCompatibility warnings\n${analysis.compatibilityWarnings.isEmpty ? 'None reported' : analysis.compatibilityWarnings.join('\n')}\n\nApktool decodes resources, manifest and Smali; it does not recover original Java/Kotlin source.',
          ),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context),
            child: const Text('Close'),
          ),
        ],
      ),
    );
  }
}
