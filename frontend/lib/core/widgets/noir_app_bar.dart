/// NOIR top app bar — 83px height, terminal icon, centered title, settings action.
library;

import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';
import '../theme/noir_colors.dart';
import '../theme/noir_typography.dart';

class NoirAppBar extends StatelessWidget implements PreferredSizeWidget {
  const NoirAppBar({
    super.key,
    this.title,
    this.leading,
    this.actions,
    this.showBackButton = false,
    this.onSettingsTap,
  });

  final String? title;
  final Widget? leading;
  final List<Widget>? actions;
  final bool showBackButton;
  final VoidCallback? onSettingsTap;

  @override
  Size get preferredSize => const Size.fromHeight(83);

  @override
  Widget build(BuildContext context) {
    return Container(
      height: 83 + MediaQuery.of(context).padding.top,
      padding: EdgeInsets.only(top: MediaQuery.of(context).padding.top),
      decoration: BoxDecoration(
        color: NoirColors.surface.withValues(alpha: 0.8),
        border: Border(
          bottom: BorderSide(color: Colors.white.withValues(alpha: 0.1)),
        ),
      ),
      child: Row(
        children: [
          const SizedBox(width: 16),
          if (showBackButton)
            IconButton(
              icon: const Icon(
                Icons.arrow_back,
                color: NoirColors.primary,
                size: 20,
              ),
              onPressed: () {
                if (context.canPop()) {
                  context.pop();
                } else {
                  context.go('/');
                }
              },
            )
          else if (leading != null)
            leading!
          else
            Image.asset(
              'assets/branding/phantom.png',
              width: 40,
              height: 40,
              semanticLabel: 'NOIR Phantom',
            ),
          const SizedBox(width: 12),
          Expanded(
            child: Text(
              title ?? 'NOIR',
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              textAlign: TextAlign.center,
              style: NoirTypography.labelCaps.copyWith(
                color: NoirColors.primary,
                fontSize: title != null ? 10 : 24,
                letterSpacing: title != null ? 1.0 : 3.2,
                fontWeight: FontWeight.w700,
                fontFamily: title != null ? 'JetBrainsMono' : 'Inter',
              ),
            ),
          ),
          const SizedBox(width: 12),
          if (actions != null)
            ...actions!
          else
            IconButton(
              tooltip: 'Connection settings',
              icon: Icon(
                Icons.sensors,
                color: NoirColors.primary.withValues(alpha: 0.5),
                size: 20,
              ),
              onPressed: onSettingsTap,
            ),
          const SizedBox(width: 16),
        ],
      ),
    );
  }
}
