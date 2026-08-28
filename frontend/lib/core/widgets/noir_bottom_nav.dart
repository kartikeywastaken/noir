/// Bottom navigation bar with glow-dot active indicator.
library;

import 'package:flutter/material.dart';
import '../theme/noir_colors.dart';
import '../theme/noir_typography.dart';

class NoirBottomNav extends StatelessWidget {
  const NoirBottomNav({
    super.key,
    required this.currentIndex,
    required this.onTap,
  });

  final int currentIndex;
  final ValueChanged<int> onTap;

  static const _items = [
    _NavItem(Icons.grid_view, 'HOME'),
    _NavItem(Icons.layers, 'PROJECTS'),
    _NavItem(Icons.work_history_outlined, 'JOBS'),
    _NavItem(Icons.settings, 'CONFIG'),
  ];

  @override
  Widget build(BuildContext context) {
    return Container(
      height: 64 + MediaQuery.of(context).padding.bottom,
      padding: EdgeInsets.only(bottom: MediaQuery.of(context).padding.bottom),
      decoration: BoxDecoration(
        color: NoirColors.surface.withValues(alpha: 0.6),
        border: Border(
          top: BorderSide(color: Colors.white.withValues(alpha: 0.1)),
        ),
      ),
      child: Row(
        mainAxisAlignment: MainAxisAlignment.spaceAround,
        children: List.generate(_items.length, (i) {
          final active = i == currentIndex;
          return GestureDetector(
            onTap: () => onTap(i),
            behavior: HitTestBehavior.opaque,
            child: SizedBox(
              width: 64,
              height: 64,
              child: Column(
                mainAxisAlignment: MainAxisAlignment.center,
                children: [
                  Icon(
                    _items[i].icon,
                    color: active
                        ? NoirColors.primary
                        : NoirColors.onSurfaceVariant.withValues(alpha: 0.5),
                    size: 24,
                  ),
                  if (active)
                    Container(
                      margin: const EdgeInsets.only(top: 4),
                      width: 4,
                      height: 4,
                      decoration: BoxDecoration(
                        color: NoirColors.primary,
                        shape: BoxShape.circle,
                        boxShadow: [
                          BoxShadow(
                            color: NoirColors.glowStrong,
                            blurRadius: 8,
                          ),
                        ],
                      ),
                    )
                  else
                    const SizedBox(height: 8),
                  Text(
                    _items[i].label,
                    style: NoirTypography.labelCaps.copyWith(fontSize: 8),
                  ),
                ],
              ),
            ),
          );
        }),
      ),
    );
  }
}

class _NavItem {
  const _NavItem(this.icon, this.label);
  final IconData icon;
  final String label;
}
