import 'package:flutter/foundation.dart';

/// Disposing a screen stops notifications, not its server-side operations.
class SafeNotifier extends ChangeNotifier {
  bool disposed = false;

  @override
  void notifyListeners() {
    if (!disposed) super.notifyListeners();
  }

  @override
  void dispose() {
    disposed = true;
    super.dispose();
  }
}
