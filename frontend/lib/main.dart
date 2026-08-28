import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import 'core/navigation/router.dart';
import 'core/state/connection_controller.dart';
import 'core/state/projects_controller.dart';
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

class _NoirAppState extends State<NoirApp> {
  final _router = createNoirRouter();
  @override
  void dispose() {
    _router.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => MultiProvider(
    providers: [
      ChangeNotifierProvider(
        create: (_) => widget.connection ?? ConnectionController(),
      ),
      ChangeNotifierProvider(
        create: (context) =>
            ProjectsController(context.read<ConnectionController>().api),
      ),
    ],
    child: MaterialApp.router(
      title: 'NOIR',
      debugShowCheckedModeBanner: false,
      theme: NoirTheme.darkTheme,
      routerConfig: _router,
    ),
  );
}
