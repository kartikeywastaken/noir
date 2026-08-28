/// Typed API exceptions with credential redaction.
class ApiException implements Exception {
  final String message;
  final int? statusCode;
  ApiException(this.message, {this.statusCode});

  @override
  String toString() => message;
}

class UnauthorizedException extends ApiException {
  UnauthorizedException([String? msg])
    : super(msg ?? 'Invalid or missing API token', statusCode: 401);
}

class NotFoundException extends ApiException {
  NotFoundException([String? msg])
    : super(msg ?? 'Resource not found', statusCode: 404);
}

class ConflictException extends ApiException {
  ConflictException([String? msg])
    : super(msg ?? 'Conflict — stale revision or duplicate', statusCode: 409);
}

class ValidationException extends ApiException {
  ValidationException([String? msg])
    : super(msg ?? 'Validation failed', statusCode: 422);
}

class ServerException extends ApiException {
  ServerException([String? msg])
    : super(msg ?? 'Server error', statusCode: 500);
}

class ConnectionException extends ApiException {
  ConnectionException([String? msg])
    : super(msg ?? 'Cannot reach backend', statusCode: null);
}
