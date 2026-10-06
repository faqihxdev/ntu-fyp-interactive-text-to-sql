export type Method = "no_user" | "sql_only_recovery" | "reactive_clarification";
export type Preview = {
  columns: string[];
  preview: unknown[][];
  status: string;
  error: string | null;
  row_count: number;
  truncated: boolean;
  source?: string;
};
export type Context = {
  role: string;
  private: boolean;
  prompt?: string;
  instructions?: string;
  output?: unknown;
  output_schema?: unknown;
  description: string;
  verified?: boolean;
  prompt_sha256?: string;
  source?: string;
  usage?: unknown;
  model?: string;
};
export type Step = {
  id: number;
  kind: "sql" | "question" | "answer";
  source: "saved" | "live";
  role: string;
  call_kind?: string;
  output: {
    sql?: string;
    question?: string;
    options?: string[];
    summary?: string;
    reviews?: Record<string, string>;
    answer?: string;
  };
  rendered_question?: string;
  candidate?: {
    sql: string;
    official_ex: boolean | null;
    status: string;
    error: string | null;
    row_count: number;
  };
  context?: Context;
  preview?: Preview;
};
export type CaseData = {
  task: {
    question_id: number;
    db_id: string;
    question: string;
    difficulty: string;
  };
  note: string;
  schema: string;
  source: string;
  record_sha256: string;
  methods: Record<
    Method,
    {
      label: string;
      description: string;
      steps: Step[];
      success: boolean;
      attempts: number;
      turns: number;
      termination: string;
    }
  >;
};
export type Catalogue = {
  run: string;
  total_tasks: number;
  groups: {
    id: string;
    label: string;
    count: number;
    description: string;
    ids: number[];
  }[];
  cases: {
    question_id: number;
    db_id: string;
    question: string;
    note: string;
    group: string;
    outcomes: Record<Method, boolean>;
  }[];
};
export type Branch = {
  id: string;
  case_id: number;
  method: Method;
  fork_step: number;
  mode: "original" | "changed";
  steps: Step[];
  phase: "reply" | "result" | "repair" | "done";
  revision: number;
  calls: number;
  attempts: number;
  answers: number;
};
export type Status = {
  token: string;
  live: {
    available: boolean;
    model: string;
    reasoning: string;
    max_calls_per_branch: number;
    max_usd: number;
    spent_usd: number | null;
  };
};
