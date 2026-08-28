/// Home screen — hero import panel + recent projects.
library;

import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';
import 'package:provider/provider.dart';
import 'package:file_picker/file_picker.dart';

import '../../core/state/connection_controller.dart';
import '../../core/state/projects_controller.dart';
import '../../core/theme/noir_colors.dart';
import '../../core/theme/noir_typography.dart';
import '../../core/widgets/corner_markers.dart';
import '../../core/widgets/glass_panel.dart';
import '../../core/widgets/mesh_gradient_background.dart';
import '../../core/widgets/noir_app_bar.dart';
import '../../core/widgets/noir_bottom_nav.dart';
import '../../core/widgets/noir_button.dart';
import '../../core/widgets/status_chip.dart';
import '../../data/models/models.dart';
import 'import_flow.dart';

class HomeScreen extends StatefulWidget {
  const HomeScreen({super.key});

  @override
  State<HomeScreen> createState() => _HomeScreenState();
}

class _HomeScreenState extends State<HomeScreen>
    with SingleTickerProviderStateMixin {
  late final AnimationController _fadeController;
  late final Animation<double> _fadeAnimation;
  late final ConnectionController _connection;
  bool _wasConnected = false;
  final _recentKey = GlobalKey();

  void _connectionChanged() {
    if (!mounted) return;
    if (_connection.isConnected && !_wasConnected) {
      context.read<ProjectsController>().loadProjects();
    }
    _wasConnected = _connection.isConnected;
  }

  @override
  void initState() {
    super.initState();
    _fadeController = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 600),
    );
    _fadeAnimation = CurvedAnimation(
      parent: _fadeController,
      curve: Curves.easeOut,
    );
    _fadeController.forward();

    _connection = context.read<ConnectionController>();
    _connection.addListener(_connectionChanged);
    Future.microtask(_connectionChanged);
  }

  @override
  void dispose() {
    _fadeController.dispose();
    _connection.removeListener(_connectionChanged);
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Consumer2<ConnectionController, ProjectsController>(
      builder: (context, conn, projects, _) {
        return Scaffold(
          backgroundColor: NoirColors.black,
          appBar: NoirAppBar(onSettingsTap: () => context.push('/settings')),
          body: MeshGradientBackground(
            child: FadeTransition(
              opacity: _fadeAnimation,
              child: ListView(
                padding: const EdgeInsets.all(16),
                children: [
                  // Hero panel
                  CornerMarkers(
                    child: GlassPanel(
                      padding: const EdgeInsets.symmetric(
                        horizontal: 32,
                        vertical: 48,
                      ),
                      child: Column(
                        children: [
                          Text(
                            'DECODE & MODIFY',
                            style: NoirTypography.headlineLg.copyWith(
                              color: NoirColors.primary,
                              fontWeight: FontWeight.w700,
                            ),
                            textAlign: TextAlign.center,
                          ),
                          const SizedBox(height: 16),
                          Text(
                            'Select an APK to decode its resource\nand bytecode structure for analysis.',
                            style: NoirTypography.codeSm.copyWith(
                              color: NoirColors.onSurfaceVariant.withValues(
                                alpha: 0.7,
                              ),
                              height: 1.6,
                            ),
                            textAlign: TextAlign.center,
                          ),
                          const SizedBox(height: 32),
                          NoirPrimaryButton(
                            label: 'Select APK',
                            icon: Icons.file_upload_outlined,
                            onPressed: conn.isConnected
                                ? () => _selectAndImport(context)
                                : null,
                          ),
                          if (!conn.isConnected) ...[
                            const SizedBox(height: 12),
                            Text(
                              'Connect to backend first',
                              style: NoirTypography.codeSm.copyWith(
                                color: NoirColors.onSurfaceVariant.withValues(
                                  alpha: 0.4,
                                ),
                              ),
                            ),
                            const SizedBox(height: 12),
                            NoirGhostButton(
                              label: 'Connect backend',
                              onPressed: () => context.push('/settings'),
                            ),
                          ],
                        ],
                      ),
                    ),
                  ),
                  const SizedBox(height: 32),

                  // Recent projects
                  if (conn.isConnected) ...[
                    Row(
                      key: _recentKey,
                      children: [
                        Icon(
                          Icons.history,
                          size: 14,
                          color: NoirColors.onSurfaceVariant.withValues(
                            alpha: 0.5,
                          ),
                        ),
                        const SizedBox(width: 8),
                        Text(
                          'RECENT WORKSPACES',
                          style: NoirTypography.labelCaps.copyWith(
                            color: NoirColors.onSurfaceVariant,
                          ),
                        ),
                        const Spacer(),
                        IconButton(
                          tooltip: 'Refresh workspaces',
                          onPressed: projects.loading
                              ? null
                              : projects.loadProjects,
                          icon: const Icon(Icons.refresh, size: 18),
                        ),
                        if (projects.loading)
                          const SizedBox(
                            width: 12,
                            height: 12,
                            child: CircularProgressIndicator(
                              strokeWidth: 1,
                              color: NoirColors.primary,
                            ),
                          ),
                      ],
                    ),
                    const SizedBox(height: 12),

                    if (projects.error != null)
                      GlassPanel(
                        padding: const EdgeInsets.all(16),
                        child: Text(
                          projects.error!,
                          style: NoirTypography.codeSm.copyWith(
                            color: NoirColors.error,
                          ),
                        ),
                      ),

                    if (projects.projects.isEmpty && !projects.loading)
                      GlassPanel(
                        padding: const EdgeInsets.all(24),
                        child: Text(
                          'No projects yet. Import an APK to get started.',
                          style: NoirTypography.bodySm.copyWith(
                            color: NoirColors.onSurfaceVariant.withValues(
                              alpha: 0.5,
                            ),
                          ),
                          textAlign: TextAlign.center,
                        ),
                      ),

                    ...projects.projects.map(
                      (p) => _buildProjectCard(context, p),
                    ),
                  ],
                ],
              ),
            ),
          ),
          bottomNavigationBar: NoirBottomNav(
            currentIndex: 0,
            onTap: (i) {
              if (i == 3) context.push('/settings');
              if (i == 2) context.push('/jobs');
              if (i == 1 && _recentKey.currentContext != null) {
                Scrollable.ensureVisible(
                  _recentKey.currentContext!,
                  duration: const Duration(milliseconds: 250),
                );
              } else if (i == 1) {
                context.push('/settings');
              }
            },
          ),
        );
      },
    );
  }

  Widget _buildProjectCard(BuildContext context, ProjectInfo project) {
    final elapsed = DateTime.now().difference(project.updatedAt);
    final timeAgo = elapsed.inHours < 1
        ? '${elapsed.inMinutes}m ago'
        : elapsed.inHours < 24
        ? '${elapsed.inHours}h ago'
        : '${elapsed.inDays}d ago';

    return Padding(
      padding: const EdgeInsets.only(bottom: 8),
      child: GlassPanel(
        onTap: () => context.push('/project/${project.id}'),
        padding: const EdgeInsets.all(16),
        child: Row(
          children: [
            Icon(
              _statusIcon(project.status),
              color: NoirColors.onSurfaceVariant.withValues(alpha: 0.5),
              size: 24,
            ),
            const SizedBox(width: 16),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    project.packageName.isNotEmpty
                        ? project.packageName
                        : project.originalFilename,
                    style: NoirTypography.codeLg.copyWith(
                      color: NoirColors.primary,
                    ),
                    overflow: TextOverflow.ellipsis,
                  ),
                  const SizedBox(height: 4),
                  Text(
                    'Modified $timeAgo',
                    style: NoirTypography.codeSm.copyWith(
                      color: NoirColors.onSurfaceVariant.withValues(alpha: 0.5),
                    ),
                  ),
                ],
              ),
            ),
            StatusChip(label: project.status),
          ],
        ),
      ),
    );
  }

  IconData _statusIcon(String status) {
    return switch (status) {
      'decoded' || 'analyzed' => Icons.folder_open,
      'building' => Icons.construction,
      'built' || 'signed' => Icons.verified_outlined,
      'exported' => Icons.archive_outlined,
      'failed' => Icons.error_outline,
      _ => Icons.description_outlined,
    };
  }

  Future<void> _selectAndImport(BuildContext context) async {
    try {
      final file = await FilePicker.pickFile(
        type: FileType.custom,
        allowedExtensions: ['apk'],
      );
      if (file == null) return;
      if (!file.name.toLowerCase().endsWith('.apk')) {
        if (!context.mounted) return;
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('Please select an APK file')),
        );
        return;
      }
      if (!context.mounted) return;
      showImportFlow(context, file);
    } catch (_) {
      if (context.mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(
            content: Text('Could not open the APK picker. Please try again.'),
          ),
        );
      }
    }
  }
}
