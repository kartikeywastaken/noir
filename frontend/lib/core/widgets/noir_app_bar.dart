/// NOIR top app bar — Material Design 3 terminal branding and status indicator.
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
    this.statusText = 'Ready',
    this.statusColor = NoirColors.primaryFixed,
    this.onSettingsTap,
  });

  final String? title;
  final Widget? leading;
  final List<Widget>? actions;
  final bool showBackButton;
  final String statusText;
  final Color statusColor;
  final VoidCallback? onSettingsTap;

  @override
  Size get preferredSize => const Size.fromHeight(64);

  @override
  Widget build(BuildContext context) {
    return Container(
      height: 64 + MediaQuery.of(context).padding.top,
      padding: EdgeInsets.only(
        top: MediaQuery.of(context).padding.top,
        left: 16,
        right: 16,
      ),
      decoration: BoxDecoration(
        color: NoirColors.surfaceDim.withValues(alpha: 0.85),
        border: Border(
          bottom: BorderSide(color: Colors.white.withValues(alpha: 0.05)),
        ),
      ),
      child: Row(
        children: [
          if (showBackButton)
            IconButton(
              icon: const Icon(
                Icons.arrow_back,
                color: NoirColors.onSurface,
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
          else ...[
            Image.asset(
              'assets/branding/phantom.png',
              width: 28,
              height: 28,
              semanticLabel: 'NOIR Phantom',
            ),
            const SizedBox(width: 8),
            const Icon(
              Icons.terminal,
              color: NoirColors.primaryFixed,
              size: 22,
            ),
          ],
          const SizedBox(width: 8),
          Expanded(
            child: Text(
              title ?? 'NOIR',
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              style: title != null && title != 'NOIR'
                  ? NoirTypography.labelCaps.copyWith(
                      color: NoirColors.primary,
                      fontSize: 12,
                      letterSpacing: 1.5,
                      fontWeight: FontWeight.w700,
                    )
                  : const TextStyle(
                      fontFamily: 'Inter',
                      fontSize: 22,
                      fontWeight: FontWeight.w900,
                      letterSpacing: -0.8,
                      color: NoirColors.primaryFixed,
                    ),
            ),
          ),
          const SizedBox(width: 8),
          if (actions != null)
            ...actions!
          else
            GestureDetector(
              onTap: onSettingsTap,
              child: Container(
                padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 5),
                decoration: BoxDecoration(
                  color: NoirColors.surfaceContainerHigh.withValues(alpha: 0.8),
                  borderRadius: BorderRadius.circular(9999),
                  border: Border.all(
                    color: Colors.white.withValues(alpha: 0.06),
                  ),
                ),
                child: Row(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    Container(
                      width: 7,
                      height: 7,
                      decoration: BoxDecoration(
                        color: statusColor,
                        shape: BoxShape.circle,
                        boxShadow: [
                          BoxShadow(
                            color: statusColor.withValues(alpha: 0.6),
                            blurRadius: 6,
                          ),
                        ],
                      ),
                    ),
                    const SizedBox(width: 6),
                    Text(
                      statusText,
                      style: TextStyle(
                        fontFamily: 'Inter',
                        fontSize: 11,
                        fontWeight: FontWeight.w600,
                        color: statusColor == NoirColors.primaryFixed
                            ? NoirColors.primaryFixed
                            : NoirColors.onSurface,
                      ),
                    ),
                  ],
                ),
              ),
            ),
        ],
      ),
    );
  }
}
