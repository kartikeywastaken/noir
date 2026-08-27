/// NOIR top app bar — 83px height, terminal icon, centered title, settings action.
import 'package:flutter/material.dart';
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
              icon: const Icon(Icons.arrow_back, color: NoirColors.primary, size: 20),
              onPressed: () => Navigator.maybePop(context),
            )
          else if (leading != null)
            leading!
          else
            IconButton(
              icon: const Icon(Icons.terminal, color: NoirColors.primary, size: 20),
              onPressed: null,
            ),
          const Spacer(),
          Text(
            title ?? 'NOIR',
            style: NoirTypography.labelCaps.copyWith(
              color: NoirColors.primary,
              fontSize: title != null ? 10 : 24,
              letterSpacing: title != null ? 1.0 : 3.2,
              fontWeight: FontWeight.w700,
              fontFamily: title != null ? 'JetBrainsMono' : 'Inter',
            ),
          ),
          const Spacer(),
          if (actions != null)
            ...actions!
          else
            IconButton(
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
