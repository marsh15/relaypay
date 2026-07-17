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

export type ScenarioStep = { key: string; label: string; status: string };

export type ScenarioResult = {
  scenario_run_id: string;
  status: "RUNNING" | "SUCCEEDED" | "NEEDS_INSPECTION";
  correlation_id: string;
  payment_intent_id: string | null;
  steps: ScenarioStep[];
  assertions: Record<string, string | number | boolean | null>;
  safe_error_code: string | null;
};
