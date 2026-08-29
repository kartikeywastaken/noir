import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';
import 'package:provider/provider.dart';
import '../../core/state/connection_controller.dart';
import '../../core/state/signing_controller.dart';
import '../../core/widgets/noir_button.dart';
import '../../core/widgets/review_layout.dart';
import '../../data/models/models.dart';
import '../../data/api/transfer_progress.dart';
import '../../core/widgets/transfer_bar.dart';

class SigningScreen extends StatefulWidget {
  const SigningScreen({
    super.key,
    required this.projectId,
    this.initialBuildId,
  });
  final String projectId;
  final String? initialBuildId;
  @override
  State<SigningScreen> createState() => _SigningScreenState();
}

class _SigningScreenState extends State<SigningScreen> {
  late final SigningController _controller;
  List<BuildResult> _builds = [];
  String? _buildId;
  int? _revision;
  bool _busy = true;
  String? _error;
  String? _saved;
  TransferProgress? _downloadProgress;
  BuildResult? get _selected =>
      _builds.where((b) => b.buildId == _buildId).firstOrNull;

  @override
  void initState() {
    super.initState();
    _buildId = widget.initialBuildId;
    _controller = SigningController(context.read<ConnectionController>().api);
    _refresh();
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  Future<void> _refresh() async {
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      await _controller.loadProfiles();
      final builds = await _controller.api.listBuilds(widget.projectId);
      final project = await _controller.api.getProject(widget.projectId);
      if (!mounted) return;
      setState(() {
        _builds = builds.where((b) => b.success).toList();
        _revision = project.workspaceRevision;
        if (!_builds.any((b) => b.buildId == _buildId)) {
          _buildId = _builds.firstOrNull?.buildId;
        }
        _error = _controller.error;
      });
    } catch (e) {
      if (mounted) setState(() => _error = e.toString());
    }
    if (mounted) setState(() => _busy = false);
  }

  Future<void> _sign() async {
    final build = _selected!;
    if (!await confirmAction(
          context,
          'Sign this build?',
          'Build: ${build.buildId}\nRevision: ${build.workspaceRevision}\nProfile: ${_controller.selectedProfile}\n\nThis signs the APK on your backend using your workspace’s key. Its certificate differs from the original app, so it cannot update an installation signed by that app’s publisher. Nothing is installed automatically.',
          'Sign',
        ) ||
        !mounted) {
      return;
    }
    setState(() {
      _busy = true;
      _error = null;
      _saved = null;
    });
    final success = await _controller.sign(widget.projectId, build.buildId);
    if (!mounted) return;
    if (success) {
      await _refresh();
    } else {
      setState(() {
        _error = _controller.error;
        _busy = false;
      });
    }
  }

  Future<void> _download() async {
    final build = _selected!;
    setState(() {
      _busy = true;
      _error = null;
      _saved = null;
      _downloadProgress = null;
    });
    try {
      final bytes = await _controller.verifiedDownload(
        widget.projectId,
        build,
        onProgress: (p) {
          if (mounted) setState(() => _downloadProgress = p);
        },
      );
      if (!mounted) return;
      final saved = await FilePicker.saveFile(
        fileName: 'noir-${widget.projectId}-${build.buildId}-signed.apk',
        mimeType: 'application/vnd.android.package-archive',
        bytes: bytes,
      );
      if (mounted) {
        setState(
          () => _saved = saved == null
              ? 'Save cancelled. No file exported.'
              : 'Verified SHA-256 and signature. Saved to:\n$saved',
        );
      }
    } catch (e) {
      if (mounted) setState(() => _error = e.toString());
    }
    if (mounted) setState(() => _busy = false);
  }

  @override
  Widget build(BuildContext context) {
    final build = _selected;
    return ReviewLayout(
      title: 'SIGN & EXPORT',
      busy: _busy,
      error: _error,
      onRefresh: _refresh,
      children: [
        if (_downloadProgress != null)
          TransferBar(
            progress: _downloadProgress!,
            title: _downloadProgress!.fraction == 1
                ? 'Received · verifying and saving'
                : 'Downloading APK',
          ),
        if (_builds.isEmpty && !_busy)
          const Section(
            title: 'NO SUCCESSFUL BUILD',
            child: Text('Validate and rebuild the workspace first.'),
          ),
        if (_builds.isNotEmpty) ...[
          Section(
            title: 'BUILD',
            child: DropdownButtonFormField<String>(
              key: ValueKey('build-$_buildId'),
              initialValue: _buildId,
              isExpanded: true,
              items: _builds
                  .map(
                    (b) => DropdownMenuItem(
                      value: b.buildId,
                      child: Text(
                        '${b.buildId} · rev ${b.workspaceRevision} · ${b.signedApkHash == null ? 'unsigned' : 'signed'}',
                        overflow: TextOverflow.ellipsis,
                      ),
                    ),
                  )
                  .toList(),
              onChanged: _busy
                  ? null
                  : (id) => setState(() {
                      _buildId = id;
                      _saved = null;
                      _error = null;
                    }),
            ),
          ),
          Section(
            title: 'SIGNING PROFILE',
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                if (_controller.profiles.isEmpty)
                  const SelectableText(
                    'Your personal signing key could not be prepared. Refresh or contact the server owner. Key material never enters the app.',
                  ),
                if (_controller.profiles.isNotEmpty)
                  DropdownButtonFormField<String>(
                    initialValue: _controller.selectedProfile,
                    isExpanded: true,
                    hint: const Text('Choose a local profile'),
                    items: _controller.profiles
                        .map(
                          (p) => DropdownMenuItem(
                            value: p.name,
                            child: Text(
                              '${p.name} · ${p.type}',
                              overflow: TextOverflow.ellipsis,
                            ),
                          ),
                        )
                        .toList(),
                    onChanged: _busy
                        ? null
                        : (name) {
                            if (name != null) {
                              setState(() => _controller.selectProfile(name));
                            }
                          },
                  ),
              ],
            ),
          ),
          if (build != null)
            Section(
              title: 'ARTIFACT',
              child: SelectableText(
                'Build revision: ${build.workspaceRevision} · Current revision: $_revision\nUnsigned SHA-256: ${build.unsignedApkHash ?? 'unavailable'}\nSigned SHA-256: ${build.signedApkHash ?? 'not signed'}',
              ),
            ),
          if (build != null && build.workspaceRevision != _revision)
            const Section(
              title: 'OLDER REVISION',
              child: Text(
                'This historical artifact can be verified/exported if already signed. Rebuild the current revision before signing.',
              ),
            ),
          Wrap(
            spacing: 12,
            runSpacing: 12,
            children: [
              NoirPrimaryButton(
                label: 'Sign build',
                onPressed:
                    _busy ||
                        build == null ||
                        build.workspaceRevision != _revision ||
                        build.signedApkHash != null ||
                        _controller.selectedProfile == null
                    ? null
                    : _sign,
              ),
              NoirPrimaryButton(
                label: 'Verify & save signed APK',
                onPressed: _busy || build?.signedApkHash == null
                    ? null
                    : _download,
              ),
            ],
          ),
        ],
        if (_saved != null)
          Padding(
            padding: const EdgeInsets.only(top: 16),
            child: Section(title: 'EXPORT', child: SelectableText(_saved!)),
          ),
        const SizedBox(height: 16),
        NoirGhostButton(
          label: 'Human-readable audit',
          onPressed: _busy
              ? null
              : () => context.push('/project/${widget.projectId}/audit'),
        ),
      ],
    );
  }
}
