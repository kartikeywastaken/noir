/// NOIR navigation router using GoRouter.
import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';

import '../../features/home/home_screen.dart';
import '../../features/settings/settings_screen.dart';
import '../../features/workspace/workspace_screen.dart';
import '../../features/plan/plan_review_screen.dart';
import '../../features/patch/patch_review_screen.dart';
import '../../features/build/build_screen.dart';
import '../../features/signing/signing_screen.dart';
import '../../features/audit/audit_screen.dart';

final noirRouter = GoRouter(
  initialLocation: '/',
  routes: [
    GoRoute(
      path: '/',
      builder: (context, state) => const HomeScreen(),
    ),
    GoRoute(
      path: '/settings',
      builder: (context, state) => const SettingsScreen(),
    ),
    GoRoute(
      path: '/project/:id',
      builder: (context, state) => WorkspaceScreen(
        projectId: state.pathParameters['id']!,
      ),
      routes: [
        GoRoute(
          path: 'plan/:planId',
          builder: (context, state) => PlanReviewScreen(
            projectId: state.pathParameters['id']!,
            planId: state.pathParameters['planId']!,
          ),
        ),
        GoRoute(
          path: 'patch/:patchId',
          builder: (context, state) => PatchReviewScreen(
            projectId: state.pathParameters['id']!,
            patchId: state.pathParameters['patchId']!,
          ),
        ),
        GoRoute(
          path: 'build',
          builder: (context, state) => BuildScreen(
            projectId: state.pathParameters['id']!,
          ),
        ),
        GoRoute(
          path: 'sign',
          builder: (context, state) => SigningScreen(
            projectId: state.pathParameters['id']!,
          ),
        ),
        GoRoute(
          path: 'audit',
          builder: (context, state) => AuditScreen(
            projectId: state.pathParameters['id']!,
          ),
        ),
      ],
    ),
  ],
);
