import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import 'core/navigation/router.dart';
import 'core/state/connection_controller.dart';
import 'core/state/projects_controller.dart';
import 'core/state/workflow_controller.dart';
import 'core/theme/noir_theme.dart';

void main() {
  WidgetsFlutterBinding.ensureInitialized();
  runApp(const NoirApp());
}

class NoirApp extends StatefulWidget {
  const NoirApp({super.key, this.connection});
  final ConnectionController? connection;
  @override
  State<NoirApp> createState() => _NoirAppState();
}

class _NoirAppState extends State<NoirApp> with WidgetsBindingObserver {
  final _router = createNoirRouter();
  late final ConnectionController _connection;
  int _sessionEpoch = 0;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    _connection = widget.connection ?? ConnectionController();
    _connection.addListener(_sessionChanged);
  }

  void _sessionChanged() {
    if (!mounted || _sessionEpoch == _connection.sessionEpoch) return;
    // Drop all cached project/route state when switching identities, including
    // requests that were in flight for the previous workspace.
    _router.go('/');
    setState(() => _sessionEpoch = _connection.sessionEpoch);
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.resumed && _connection.hasToken) {
      _connection.testConnection();
    }
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    _connection.removeListener(_sessionChanged);
    _connection.dispose();
    _router.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => ChangeNotifierProvider.value(
    value: _connection,
    child: KeyedSubtree(
      key: ValueKey(_sessionEpoch),
      child: MultiProvider(
        providers: [
          ChangeNotifierProvider(
            create: (_) => ProjectsController(_connection.api),
          ),
          ChangeNotifierProvider(
            create: (_) => WorkflowController(_connection.api),
          ),
        ],
        child: MaterialApp.router(
          title: 'NOIR',
          debugShowCheckedModeBanner: false,
          theme: NoirTheme.darkTheme,
          routerConfig: _router,
        ),
      ),
    ),
  );
}
