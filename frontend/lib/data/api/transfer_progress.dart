class TransferProgress {
  const TransferProgress(this.bytes, this.total);
  final int bytes;
  final int? total;
  double? get fraction =>
      total != null && total! > 0 ? (bytes / total!).clamp(0.0, 1.0) : null;
  String get label {
    String mb(int value) => '${(value / 1048576).toStringAsFixed(1)} MB';
    if (fraction == null) return '${mb(bytes)} transferred · total unknown';
    final left = (total! - bytes).clamp(0, total!);
    return '${(fraction! * 100).floor()}% · ${mb(bytes)} / ${mb(total!)} · ${mb(left)} left';
  }
}

typedef TransferCallback = void Function(TransferProgress progress);
