export class AppError extends Error {
  constructor(
    message: string,
    readonly statusCode = 400,
    readonly code = "app_error",
  ) {
    super(message);
    this.name = "AppError";
  }
}

export class ForbiddenError extends AppError {
  constructor(message = "Ação não permitida para o seu papel.") {
    super(message, 403, "forbidden");
  }
}

export class NotFoundError extends AppError {
  constructor(message = "Recurso não encontrado.") {
    super(message, 404, "not_found");
  }
}
