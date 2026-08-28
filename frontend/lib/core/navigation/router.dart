import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';
import 'package:provider/provider.dart';
import '../state/connection_controller.dart';
import '../state/workspace_controller.dart';
import '../state/build_controller.dart';
import '../../features/home/home_screen.dart';
import '../../features/settings/settings_screen.dart';
import '../../features/workspace/workspace_screen.dart';
import '../../features/plan/plan_review_screen.dart';
import '../../features/patch/patch_review_screen.dart';
import '../../features/build/build_screen.dart';
import '../../features/signing/signing_screen.dart';
import '../../features/audit/audit_screen.dart';
import '../../features/history/history_screen.dart';
import '../../features/history/build_history_screen.dart';

GoRouter createNoirRouter() => GoRouter(
  routes: [
    GoRoute(path: '/', builder: (_, _) => const HomeScreen()),
    GoRoute(path: '/settings', builder: (_, _) => const SettingsScreen()),
    GoRoute(path: '/history', builder: (_, _) => const BuildHistoryScreen()),
    GoRoute(path: '/jobs', redirect: (_, _) => '/history'),
    GoRoute(
      path: '/project/:id',
      builder: (context, state) => ChangeNotifierProvider(
        key: ValueKey(state.pathParameters['id']),
        create: (_) =>
            WorkspaceController(context.read<ConnectionController>().api),
        child: WorkspaceScreen(projectId: state.pathParameters['id']!),
      ),
      routes: [
        GoRoute(
          path: 'plan/:planId',
          builder: (_, state) => PlanReviewScreen(
            projectId: state.pathParameters['id']!,
            planId: state.pathParameters['planId']!,
          ),
        ),
        GoRoute(
          path: 'patch/:patchId',
          builder: (_, state) => PatchReviewScreen(
            projectId: state.pathParameters['id']!,
            patchId: state.pathParameters['patchId']!,
          ),
        ),
        GoRoute(
          path: 'build',
          builder: (context, state) => ChangeNotifierProvider(
            create: (_) =>
                BuildController(context.read<ConnectionController>().api),
            child: BuildScreen(projectId: state.pathParameters['id']!),
          ),
        ),
        GoRoute(
          path: 'sign',
          builder: (_, state) => SigningScreen(
            projectId: state.pathParameters['id']!,
            initialBuildId: state.uri.queryParameters['build'],
          ),
        ),
        GoRoute(
          path: 'audit',
          builder: (_, state) =>
              AuditScreen(projectId: state.pathParameters['id']!),
        ),
        GoRoute(
          path: 'history',
          builder: (_, state) =>
              HistoryScreen(projectId: state.pathParameters['id']!),
        ),
      ],
    ),
  ],
);
