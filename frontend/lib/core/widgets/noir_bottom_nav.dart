/// Bottom navigation bar with Electric Lime active pill indicator.
library;

import 'package:flutter/material.dart';
import '../theme/noir_colors.dart';

class NoirBottomNav extends StatelessWidget {
  const NoirBottomNav({
    super.key,
    required this.currentIndex,
    required this.onTap,
  });

  final int currentIndex;
  final ValueChanged<int> onTap;

  static const _items = [
    _NavItem(Icons.grid_view, 'Home'),
    _NavItem(Icons.layers, 'Workspace'),
    _NavItem(Icons.settings, 'Settings'),
  ];

  @override
  Widget build(BuildContext context) {
    return Container(
      height: 72 + MediaQuery.of(context).padding.bottom,
      padding: EdgeInsets.only(
        bottom: MediaQuery.of(context).padding.bottom + 6,
        top: 6,
        left: 12,
        right: 12,
      ),
      decoration: BoxDecoration(
        color: NoirColors.surfaceContainer.withValues(alpha: 0.95),
        border: Border(
          top: BorderSide(color: Colors.white.withValues(alpha: 0.06)),
        ),
      ),
      child: Row(
        mainAxisAlignment: MainAxisAlignment.spaceAround,
        children: List.generate(_items.length, (i) {
          final active = i == currentIndex;
          final item = _items[i];
          return GestureDetector(
            onTap: () => onTap(i),
            behavior: HitTestBehavior.opaque,
            child: AnimatedContainer(
              duration: const Duration(milliseconds: 200),
              padding: EdgeInsets.symmetric(
                horizontal: active ? 18 : 12,
                vertical: 6,
              ),
              decoration: BoxDecoration(
                color: active ? NoirColors.primaryFixed : Colors.transparent,
                borderRadius: BorderRadius.circular(16),
                boxShadow: active
                    ? [
                        BoxShadow(
                          color: NoirColors.primaryFixed.withValues(alpha: 0.25),
                          blurRadius: 12,
                        ),
                      ]
                    : null,
              ),
              child: Column(
                mainAxisSize: MainAxisSize.min,
                mainAxisAlignment: MainAxisAlignment.center,
                children: [
                  Icon(
                    item.icon,
                    color: active ? NoirColors.onPrimaryFixed : NoirColors.onSurfaceVariant,
                    size: 22,
                  ),
                  const SizedBox(height: 2),
                  Text(
                    item.label,
                    style: TextStyle(
                      fontFamily: 'Inter',
                      fontSize: 11,
                      fontWeight: active ? FontWeight.w700 : FontWeight.w500,
                      color: active ? NoirColors.onPrimaryFixed : NoirColors.onSurfaceVariant,
                    ),
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
