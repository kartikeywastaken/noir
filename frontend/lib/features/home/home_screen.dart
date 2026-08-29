import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';
import 'package:provider/provider.dart';
import '../../core/state/connection_controller.dart';
import '../../core/state/workflow_controller.dart';
import '../../core/state/signing_controller.dart';
import '../../core/widgets/noir_bottom_nav.dart';
import '../../core/widgets/noir_app_bar.dart';
import '../../core/widgets/noir_button.dart';
import '../../core/widgets/review_layout.dart';
import '../../core/widgets/transfer_bar.dart';
import '../../data/api/transfer_progress.dart';

class HomeScreen extends StatefulWidget {
  const HomeScreen({super.key, this.projectId});
  final String? projectId;
  @override
  State<HomeScreen> createState() => _HomeScreenState();
}

class _HomeScreenState extends State<HomeScreen> {
  final _request = TextEditingController();
  bool _consent = false, _downloading = false;
  TransferProgress? _download;
  String? _saveMessage;
  @override
  void initState() {
    super.initState();
    if (widget.projectId != null) {
      WidgetsBinding.instance.addPostFrameCallback((_) async {
        if (!mounted) return;
        final flow = context.read<WorkflowController>();
        await flow.resume(widget.projectId!);
        if (mounted) _request.text = flow.request;
      });
    } else {
      _request.text = context.read<WorkflowController>().request;
    }
  }

  @override
  void dispose() {
    _request.dispose();
    super.dispose();
  }

  Future<void> _select() async {
    try {
      final files = await FilePicker.pickFiles(
        type: FileType.custom,
        allowedExtensions: ['apk'],
      );
      if (!mounted || files.isEmpty) return;
      _saveMessage = null;
      _download = null;
      _request.clear();
      await context.read<WorkflowController>().importFile(files.single);
    } catch (e) {
      if (mounted) setState(() => _saveMessage = '$e');
    }
  }

  Future<void> _save(WorkflowController flow) async {
    final build = flow.build!;
    final signing = SigningController(context.read<ConnectionController>().api);
    setState(() {
      _downloading = true;
      _download = null;
      _saveMessage = null;
    });
    try {
      final bytes = await signing.verifiedDownload(
        build.projectId,
        build,
        onProgress: (p) {
          if (mounted) setState(() => _download = p);
        },
      );
      if (!mounted) return;
      final saved = await FilePicker.saveFile(
        fileName: 'noir-${build.buildId}-signed.apk',
        mimeType: 'application/vnd.android.package-archive',
        bytes: bytes,
      );
      if (mounted) {
        setState(
          () => _saveMessage = saved == null
              ? 'Save cancelled. Download again whenever you need it.'
              : 'Verified APK saved to $saved',
        );
      }
    } catch (e) {
      if (mounted) setState(() => _saveMessage = '$e');
    } finally {
      signing.dispose();
      if (mounted) setState(() => _downloading = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final connection = context.watch<ConnectionController>();
    final flow = context.watch<WorkflowController>();
    return Scaffold(
      appBar: NoirAppBar(onSettingsTap: () => context.go('/settings')),
      bottomNavigationBar: NoirBottomNav(
        currentIndex: 0,
        onTap: (i) {
          if (i == 1) context.go('/history');
          if (i == 2) context.go('/settings');
        },
      ),
      body: ListView(
        padding: const EdgeInsets.all(20),
        children: [
          const Text(
            'YOUR APK. YOUR CHANGES.',
            style: TextStyle(fontSize: 23, fontWeight: FontWeight.bold),
          ),
          const SizedBox(height: 16),
          Row(
            children: [
              for (var i = 0; i < 3; i++)
                Expanded(
                  child: Container(
                    padding: const EdgeInsets.symmetric(vertical: 14),
                    decoration: BoxDecoration(
                      border: Border(
                        bottom: BorderSide(
                          width: flow.step == i ? 3 : 1,
                          color: flow.step == i ? Colors.white : Colors.white24,
                        ),
                      ),
                    ),
                    child: Text(
                      '${i + 1}  ${['APK', 'CHANGES', 'DOWNLOAD'][i]}',
                      textAlign: TextAlign.center,
                      style: const TextStyle(fontSize: 11),
                    ),
                  ),
                ),
            ],
          ),
          const SizedBox(height: 20),
          if (!connection.isConnected)
            Section(
              title: 'YOUR PRIVATE WORKSPACE',
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  Text(
                    connection.hasToken
                        ? 'Connection unavailable or reconnecting…'
                        : 'Activate your private workspace',
                  ),
                  if (connection.errorMessage != null)
                    Text(connection.errorMessage!),
                  const SizedBox(height: 12),
                  NoirPrimaryButton(
                    label: connection.hasToken
                        ? 'Connection settings'
                        : 'Enter invitation',
                    onPressed: () => context.go('/settings'),
                  ),
                ],
              ),
            ),
          if (flow.step == 0)
            Section(
              title: '1 / CHOOSE YOUR APK',
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  const Text(
                    'Select an APK you own or have permission to modify. NOIR uploads and decodes it automatically.',
                  ),
                  const SizedBox(height: 18),
                  NoirPrimaryButton(
                    label: 'Select APK',
                    icon: Icons.upload_file,
                    onPressed: connection.isConnected && !flow.working
                        ? _select
                        : null,
                  ),
                  const SizedBox(height: 10),
                  const Text(
                    'By choosing a file, you confirm you are authorized to process it.',
                  ),
                  if (flow.filename.isNotEmpty) Text(flow.filename),
                  if (flow.upload != null)
                    TransferBar(
                      progress: flow.upload!,
                      title: flow.upload!.fraction == 1
                          ? 'Upload sent · waiting for the server'
                          : 'Uploading APK',
                    ),
                ],
              ),
            ),
          if (flow.step == 1)
            Section(
              title: '2 / DESCRIBE & REVIEW',
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  Text(flow.filename),
                  if (!flow.previewReady) ...[
                    const SizedBox(height: 14),
                    TextField(
                      controller: _request,
                      enabled: !flow.working,
                      minLines: 3,
                      maxLines: 6,
                      decoration: const InputDecoration(
                        labelText: 'What would you like to change?',
                        hintText: 'For example: change the app name to a1b2c3',
                      ),
                    ),
                    CheckboxListTile(
                      contentPadding: EdgeInsets.zero,
                      value: _consent,
                      onChanged: flow.working
                          ? null
                          : (v) => setState(() => _consent = v ?? false),
                      title: const Text(
                        'Allow relevant decoded APK content to be sent to Gemini for these changes.',
                      ),
                    ),
                    NoirPrimaryButton(
                      label: 'Preview changes',
                      onPressed:
                          !flow.working && _consent && connection.isConnected
                          ? () => flow.prepare(_request.text)
                          : null,
                    ),
                  ] else ...[
                    const SizedBox(height: 12),
                    Text(
                      flow.plan!.intendedOutcome,
                      style: const TextStyle(fontWeight: FontWeight.bold),
                    ),
                    for (final risk in flow.plan!.risks) Text('• $risk'),
                    for (final concern in flow.plan!.compatibilityConcerns)
                      Text('• $concern'),
                    for (final limitation in flow.plan!.unsupportedAspects)
                      Text('Not supported: $limitation'),
                    for (final permission in flow.plan!.permissionChanges)
                      Text('Permission: $permission'),
                    for (final behavior in flow.plan!.behavioralChanges)
                      Text('Behavior: $behavior'),
                    for (final field in [
                      'network_destinations',
                      'data_categories',
                      'runtime_triggers',
                      'background_behavior',
                    ])
                      for (final item in (flow.plan!.raw[field] as List? ?? []))
                        Text('$field: $item'),
                    for (final entry in flow.diff)
                      ExpansionTile(
                        tilePadding: EdgeInsets.zero,
                        title: Text(entry.path),
                        subtitle: Text(entry.operation),
                        children: [
                          SelectableText(
                            entry.preview.isNotEmpty
                                ? entry.preview
                                : '${entry.before ?? ''}\n→\n${entry.after ?? ''}',
                          ),
                        ],
                      ),
                    const SizedBox(height: 14),
                    const Text(
                      'Approve these exact edits and sign the result with your private workspace key. '
                      'The new certificate cannot update the publisher’s installed app. Nothing is installed automatically.',
                    ),
                    const SizedBox(height: 16),
                    NoirPrimaryButton(
                      label: 'Approve & make APK',
                      onPressed: flow.working ? null : flow.approveAndBuild,
                    ),
                    TextButton(
                      onPressed: flow.working
                          ? null
                          : () {
                              _request.text = flow.request;
                              flow.editRequest();
                            },
                      child: const Text('Edit request'),
                    ),
                  ],
                ],
              ),
            ),
          if (flow.step == 2)
            Section(
              title: '3 / YOUR NEW APK',
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  Text(flow.filename),
                  if (flow.build == null)
                    const Text(
                      'Applying your approved changes, validating, rebuilding and signing.',
                    ),
                  if (flow.build != null) ...[
                    const Text(
                      'Your signed APK is ready. Download verifies its signature and file integrity.',
                    ),
                    const SizedBox(height: 16),
                    NoirPrimaryButton(
                      label: 'Download APK',
                      loading: _downloading,
                      onPressed: _downloading ? null : () => _save(flow),
                    ),
                    if (_download != null)
                      TransferBar(
                        progress: _download!,
                        title: _download!.fraction == 1
                            ? 'Received · verifying and saving'
                            : 'Downloading APK',
                      ),
                    if (_downloading && _download == null)
                      const Text('Checking APK signature…'),
                    TextButton(
                      onPressed: _downloading
                          ? null
                          : () {
                              flow.reset();
                              _request.clear();
                              setState(() {
                                _download = null;
                                _saveMessage = null;
                              });
                              context.go('/');
                            },
                      child: const Text('Make another APK'),
                    ),
                  ],
                  if (flow.retryFinish)
                    NoirPrimaryButton(
                      label: 'Retry remaining build steps',
                      onPressed: flow.working ? null : flow.approveAndBuild,
                    ),
                ],
              ),
            ),
          if (flow.job != null && !flow.job!.isTerminal)
            Section(
              title: 'PROCESSING',
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  const LinearProgressIndicator(),
                  const SizedBox(height: 12),
                  Text(
                    flow.job!.state == 'queued'
                        ? 'Queued · waiting for the server worker'
                        : flow.job!.stage.replaceAll('_', ' '),
                  ),
                  const Text(
                    'You can leave this screen. Resume from History; do not upload again.',
                  ),
                  TextButton(
                    onPressed: flow.cancel,
                    child: const Text('Cancel operation'),
                  ),
                ],
              ),
            ),
          if (flow.error != null)
            Section(
              title: 'STATUS',
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  SelectableText(flow.error!),
                  TextButton(
                    onPressed: flow.refresh,
                    child: const Text('Refresh status'),
                  ),
                ],
              ),
            ),
          if (_saveMessage != null)
            Section(title: 'DOWNLOAD', child: SelectableText(_saveMessage!)),
          if (flow.project != null && !flow.working)
            ExpansionTile(
              title: const Text('Advanced tools'),
              children: [
                TextButton(
                  onPressed: () => context.push('/project/${flow.project!.id}'),
                  child: const Text('Manual editing & detailed audit'),
                ),
              ],
            ),
        ],
      ),
    );
  }
}
