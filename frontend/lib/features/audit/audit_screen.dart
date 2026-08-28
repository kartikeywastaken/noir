import 'dart:convert';
import 'dart:typed_data';
import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';
import 'package:flutter_markdown_plus/flutter_markdown_plus.dart';
import 'package:provider/provider.dart';
import '../../core/state/connection_controller.dart';
import '../../core/widgets/noir_button.dart';
import '../../core/widgets/review_layout.dart';

class AuditScreen extends StatefulWidget {
  const AuditScreen({super.key, required this.projectId});
  final String projectId;
  @override
  State<AuditScreen> createState() => _AuditScreenState();
}

class _AuditScreenState extends State<AuditScreen> {
  bool _busy = true;
  String? _markdown;
  String? _json;
  String? _error;
  String? _saved;
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
      final markdown = await api.getAuditMarkdown(widget.projectId);
      final json = await api.getAuditJson(widget.projectId);
      if (mounted) {
        setState(() {
          _markdown = markdown;
          _json = const JsonEncoder.withIndent('  ').convert(json);
        });
      }
    } catch (e) {
      if (mounted) setState(() => _error = e.toString());
    }
    if (mounted) setState(() => _busy = false);
  }

  Future<void> _save(bool json) async {
    setState(() {
      _busy = true;
      _error = null;
      _saved = null;
    });
    try {
      final saved = await FilePicker.saveFile(
        fileName: 'noir-${widget.projectId}-audit.${json ? 'json' : 'md'}',
        bytes: Uint8List.fromList(utf8.encode(json ? _json! : _markdown!)),
        mimeType: json ? 'application/json' : 'text/markdown',
      );
      if (mounted) {
        setState(
          () => _saved = saved == null ? 'Save cancelled.' : 'Saved: $saved',
        );
      }
    } catch (e) {
      if (mounted) setState(() => _error = e.toString());
    }
    if (mounted) setState(() => _busy = false);
  }

  @override
  Widget build(BuildContext context) => ReviewLayout(
    title: 'AUDIT REPORT',
    busy: _busy,
    error: _error,
    onRefresh: _load,
    children: [
      Wrap(
        spacing: 12,
        runSpacing: 12,
        children: [
          NoirGhostButton(
            label: 'Save Markdown',
            onPressed: _busy || _markdown == null ? null : () => _save(false),
          ),
          NoirGhostButton(
            label: 'Save JSON',
            onPressed: _busy || _json == null ? null : () => _save(true),
          ),
        ],
      ),
      const SizedBox(height: 16),
      if (_saved != null)
        Section(title: 'EXPORT', child: SelectableText(_saved!)),
      if (_markdown != null)
        Section(
          title: 'BACKEND AUDIT · MARKDOWN',
          child: MarkdownBody(
            data: _markdown!
                .replaceAll('✅', 'PASS')
                .replaceAll('❌', 'FAIL')
                .replaceAll('⚠️', 'WARNING'),
            selectable: true,
            // Reports may contain user/AI text; never fetch embedded remote images.
            imageBuilder: (uri, title, alt) => Text(alt ?? '[image omitted]'),
          ),
        ),
    ],
  );
}
