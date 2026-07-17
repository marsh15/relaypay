export type MoneyEquation = {
  captured: number;
  succeededRefunds: number;
  reservedRefunds: number;
  available: number;
};

export type Evidence = {
  paymentIntent: {
    id: string;
    merchantReference: string;
    amount: number;
    currency: "INR";
    createdAt: string;
    status: string;
    refundAvailability: MoneyEquation;
  };
  resources: Array<{
    id: string;
    type: string;
    status: string;
    amount: number;
    currency: string;
  }>;
  idempotency: Array<{
    keyHint: string | null;
    fingerprintSummary: Record<string, unknown>;
    isTerminal: boolean;
    responseSha256: string | null;
  }>;
  providerOperations: ProviderOperationEvidence[];
  providerAttempts: ProviderAttemptEvidence[];
  operationHistory: OperationHistoryEvidence[];
  ledger: {
    journals: Array<{ id: string; type: string; currency: string }>;
    postings: Array<{
      journalId: string;
      accountCode: string;
      side: "DEBIT" | "CREDIT";
      amount: number;
      currency: string;
    }>;
  };
  events: Array<{ id: string; type: string; sha256: string }>;
  recipients: Array<{ id: string; eventId: string; endpointVersionId: string }>;
  deliveries: DeliveryEvidence[];
  deliveryAttempts: Array<{
    deliveryId: string;
    sequence: number;
    result: string;
    eventSha256: string;
    httpStatus: number | null;
    safeErrorCode: string | null;
  }>;
  limits: { perCollection: number };
};

export type ProviderOperationEvidence = {
  id: string;
  kind: string;
  stableProviderKey: string;
  status: string;
  attemptCount: number;
  requestSha256: string | null;
  responseSha256: string | null;
  finalizedAt: string | null;
};

export type ProviderAttemptEvidence = {
  operationId: string;
  sequence: number;
  kind: string;
  state: string;
  requestSha256: string;
  responseSha256: string | null;
  httpStatus: number | null;
  classification: string | null;
  safeErrorCode: string | null;
};

export type OperationHistoryEvidence = {
  operationId: string;
  from: string | null;
  to: string;
  reason: string;
  actor: string;
  correlationId: string;
};

export type DeliveryEvidence = {
  id: string;
  status: string;
  attemptCount: number;
  deliveredAt: string | null;
  deadLetteredAt: string | null;
};
