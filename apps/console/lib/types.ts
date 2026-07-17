export type Session = {
  userId: string | null;
  displayName: string;
  organisationId: string;
  csrfToken: string;
  expiresAt: string | null;
};

export type ApiError = {
  error?: {
    code?: string;
    message?: string;
  };
};
