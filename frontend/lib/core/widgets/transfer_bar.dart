import 'package:flutter/material.dart';
import '../../data/api/transfer_progress.dart';

class TransferBar extends StatelessWidget {
  const TransferBar({super.key, required this.progress, required this.title});
  final TransferProgress progress;
  final String title;
  @override
  Widget build(BuildContext context) => Padding(
    padding: const EdgeInsets.symmetric(vertical: 12),
    child: Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(title),
        const SizedBox(height: 8),
        LinearProgressIndicator(
          value: progress.fraction,
          semanticsLabel: title,
        ),
        const SizedBox(height: 8),
        Text(progress.label),
      ],
    ),
  );
}
