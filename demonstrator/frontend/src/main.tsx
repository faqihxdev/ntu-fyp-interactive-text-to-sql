import { useEffect, useRef, useState, type ReactNode } from "react";
import { createRoot } from "react-dom/client";
import {
  ArrowLeft,
  ArrowRight,
  Check,
  ChevronDown,
  CircleCheck,
  Code2,
  Database,
  FileText,
  GitBranch,
  Info,
  Layers3,
  ListEnd,
  LoaderCircle,
  MessageSquare,
  Pause,
  Play,
  RotateCcw,
  Search,
  Send,
  ShieldCheck,
  Sparkles,
  X,
} from "lucide-react";
import { api } from "./api";
import { Sql, SqlChange, PreviewTable } from "./query-view";
import { Sandbox } from "./sandbox";
import { StudyDetails } from "./study-details";
import type {
  Branch,
  CaseData,
  Catalogue,
  Context,
  Method,
  Preview,
  Status,
  Step,
} from "./types";
import "./style.css";
const methods: Method[] = [
  "no_user",
  "sql_only_recovery",
  "reactive_clarification",
];
const outcomeLabels: Record<Method, string> = {
  no_user: "Initial-attempt baseline",
  sql_only_recovery: "SQL-only recovery",
  reactive_clarification: "Reactive clarification",
};
const errorMessage = (error: unknown) =>
  error instanceof Error ? error.message : "Something went wrong.";
function DisclosureTitle({ children }: { children: ReactNode }) {
  return (
    <>
      <span>{children}</span>
      <ChevronDown
        size={14}
        className="disclosure-chevron"
        aria-hidden="true"
      />
    </>
  );
}

function CaseLegend() {
  const details = useRef<HTMLDetailsElement>(null);
  useEffect(() => {
    const closeOutside = (event: PointerEvent) => {
      if (
        event.target instanceof Node &&
        !details.current?.contains(event.target)
      ) {
        details.current?.removeAttribute("open");
      }
    };
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape" && details.current?.open) {
        details.current.open = false;
        details.current.querySelector("summary")?.focus();
      }
    };
    document.addEventListener("pointerdown", closeOutside);
    document.addEventListener("keydown", closeOnEscape);
    return () => {
      document.removeEventListener("pointerdown", closeOutside);
      document.removeEventListener("keydown", closeOnEscape);
    };
  }, []);
  return (
    <details className="case-legend" ref={details}>
      <summary aria-label="Case result legend" title="Case result legend">
        <Info size={18} aria-hidden="true" />
      </summary>
      <div className="case-legend-panel">
        <strong>Indicators, left to right</strong>
        <ol>
          <li>Initial-attempt baseline</li>
          <li>SQL-only recovery</li>
          <li>Reactive clarification</li>
        </ol>
        <div className="case-legend-key">
          <span>
            <Check size={14} className="text-success" aria-hidden="true" />
            BIRD match
          </span>
          <span>
            <X size={14} className="text-miss" aria-hidden="true" />
            No match
          </span>
        </div>
      </div>
    </details>
  );
}

function Badge({ match }: { match: boolean | null }) {
  return (
    <span
      className={
        "badge " + (match === null ? "neutral" : match ? "success" : "miss")
      }
    >
      {match === null ? (
        "Not gold-scored"
      ) : match ? (
        <>
          <Check size={13} />
          BIRD match
        </>
      ) : (
        <>No BIRD match</>
      )}
    </span>
  );
}
function cleanAnswer(value: string) {
  return value
    .replace(/^\s*answer_to_cq\s*=\s*/i, "")
    .replace(/^(["'])([\s\S]*)\1$/, "$2");
}

function ReviewSummary({ value }: { value: unknown }) {
  if (
    !value ||
    typeof value !== "object" ||
    !("summary" in value) ||
    typeof value.summary !== "string"
  )
    return null;
  const reviews =
    "reviews" in value && value.reviews && typeof value.reviews === "object"
      ? value.reviews
      : {};
  const labels: Record<string, string> = {
    AmbQuestion: "Question",
    AmbTableColumn: "Tables & columns",
    AmbOutput: "Output",
    AmbValue: "Values",
  };
  return (
    <section className="mb-4 space-y-3 rounded-lg bg-raised p-4">
      <h3 className="text-sm">Summary & review</h3>
      <p className="text-sm text-ink">{value.summary}</p>
      <dl className="grid gap-3 text-xs sm:grid-cols-2">
        {Object.entries(reviews)
          .filter(([, v]) => typeof v === "string")
          .map(([k, v]) => (
            <div key={k}>
              <dt className="mb-1 font-semibold">{labels[k] || k}</dt>
              <dd className="leading-relaxed text-muted">{String(v)}</dd>
            </div>
          ))}
      </dl>
    </section>
  );
}
function App() {
  const [view, setView] = useState<"saved" | "custom">("saved");
  const [customBusy, setCustomBusy] = useState(false);
  const [catalogue, setCatalogue] = useState<Catalogue>();
  const [status, setStatus] = useState<Status>();
  const [caseId, setCaseId] = useState(780);
  const [data, setData] = useState<CaseData>();
  const [method, setMethod] = useState<Method>("reactive_clarification");
  const [cursor, setCursor] = useState(1);
  const [playing, setPlaying] = useState(false);
  const [search, setSearch] = useState("");
  const [error, setError] = useState("");
  const [branch, setBranch] = useState<Branch>();
  const [forkStep, setForkStep] = useState<number>();
  const [mode, setMode] = useState<"original" | "changed">("original");
  const [reply, setReply] = useState("");
  const [suggestion, setSuggestion] = useState("");
  const [busy, setBusy] = useState("");
  const [context, setContext] = useState<{ title: string; value: Context }>();
  const [showContext, setShowContext] = useState(false);
  const dialog = useRef<HTMLDialogElement>(null);
  const contextDialog = useRef<HTMLDialogElement>(null);
  const generation = useRef(0);
  const [previews, setPreviews] = useState<Record<string, Preview>>({});
  const [expandedPreview, setExpandedPreview] = useState<
    Record<string, boolean>
  >({});

  useEffect(() => {
    Promise.all([api<Catalogue>("/catalogue"), api<Status>("/status")])
      .then(([c, s]) => {
        setCatalogue(c);
        setStatus(s);
      })
      .catch((e) => setError(errorMessage(e)));
  }, []);
  useEffect(() => {
    let active = true;
    generation.current++;
    setData(undefined);
    setError("");
    setBranch(undefined);
    setCursor(1);
    setPlaying(false);
    setReply("");
    setSuggestion("");
    setShowContext(false);
    setExpandedPreview({});
    api<CaseData>(`/cases/${caseId}`)
      .then((d) => {
        if (active) {
          setData(d);
          setCursor(d.methods[method].steps.length);
        }
      })
      .catch((e) => {
        if (active) setError(errorMessage(e));
      });
    return () => {
      active = false;
    };
  }, [caseId]);
  const steps = branch?.steps || data?.methods[method].steps || [];
  const canUseLiveBranch = !!status?.live.available && status.live.branch_inputs_ready !== false;
  const visible = branch ? steps : steps.slice(0, cursor);
  useEffect(() => {
    if (!playing || branch) return;
    const timer = window.setInterval(
      () =>
        setCursor((c) => {
          if (c >= steps.length) {
            setPlaying(false);
            return c;
          }
          return c + 1;
        }),
      1800,
    );
    return () => clearInterval(timer);
  }, [playing, steps.length, branch]);
  useEffect(() => {
    if (forkStep !== undefined) dialog.current?.showModal();
    else dialog.current?.close();
  }, [forkStep]);
  useEffect(() => {
    if (showContext) contextDialog.current?.showModal();
    else contextDialog.current?.close();
  }, [showContext]);
  function selectMethod(m: Method) {
    generation.current++;
    setMethod(m);
    setBranch(undefined);
    setPlaying(false);
    setCursor(data?.methods[m].steps.length ?? 1);
    setError("");
    setReply("");
    setSuggestion("");
    setShowContext(false);
  }
  async function inspect(step: Step) {
    setBusy(`context-${step.id}`);
    const epoch = generation.current;
    try {
      const value =
        step.context ||
        (await api<Context>(`/cases/${caseId}/${method}/${step.id}/context`));
      if (epoch === generation.current) {
        setContext({
          title: `${step.source === "live" ? "Live" : "Saved"} ${step.kind} context`,
          value,
        });
        setShowContext(true);
      }
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy("");
    }
  }
  async function preview(step: Step) {
    const key = `${caseId}-${method}-${branch?.id || "saved"}-${step.id}`;
    if (expandedPreview[key]) {
      setExpandedPreview((v) => ({ ...v, [key]: false }));
      return;
    }
    setBusy(`preview-${step.id}`);
    try {
      if (!previews[key]) {
        const value =
          step.preview ||
          (await api<Preview>(`/cases/${caseId}/${method}/${step.id}/preview`));
        setPreviews((v) => ({ ...v, [key]: value }));
      }
      setExpandedPreview((v) => ({ ...v, [key]: true }));
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy("");
    }
  }
  async function fork() {
    if (forkStep === undefined || !status) return;
    setBusy("fork");
    setError("");
    try {
      const b = await api<Branch>(
        "/branches",
        { case_id: caseId, method, step_id: forkStep, mode },
        status.token,
      );
      setBranch(b);
      setForkStep(undefined);
      setReply("");
      setSuggestion("");
      setPlaying(false);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy("");
    }
  }
  async function act(action: string, text = "") {
    if (!branch || !status || busy) return;
    setBusy(action);
    setError("");
    try {
      const result = await api<{ session: Branch; suggestion?: string }>(
        `/branches/${branch.id}/actions`,
        {
          revision: branch.revision,
          request_id: crypto.randomUUID(),
          action,
          text,
        },
        status.token,
      );
      setBranch(result.session);
      if (result.suggestion) setSuggestion(result.suggestion);
      else {
        setReply("");
        setSuggestion("");
      }
      api<Status>("/status")
        .then(setStatus)
        .catch(() => {});
    } catch (e) {
      setError(errorMessage(e));
      try {
        setBranch(await api<Branch>(`/branches/${branch.id}`));
      } catch {
        /* keep local state when server is unavailable */
      }
    } finally {
      setBusy("");
    }
  }
  const selectedGroup = catalogue?.groups.find((g) => g.ids.includes(caseId));
  const isLiveBusy =
    !!busy && ["reply", "ask", "repair", "suggest", "stop"].includes(busy);

  return (
    <>
      <a className="skip" href={view === "saved" ? "#main" : "#custom-main"}>
        Skip to the content
      </a>
      <header className="topbar">
        <div className="app-heading">
          <div className="brand">
            <span className="brand-icon">
              <Layers3 size={20} />
            </span>
            <span>SQL interaction explorer</span>
          </div>
          <nav className="view-switch" aria-label="Explorer mode">
            <button
              aria-current={view === "saved" ? "page" : undefined}
              disabled={isLiveBusy || customBusy}
              onClick={() => {
                setView("saved");
                setPlaying(false);
              }}
            >
              <FileText size={16} />
              Saved cases
            </button>
            <button
              aria-current={view === "custom" ? "page" : undefined}
              disabled={isLiveBusy || customBusy}
              onClick={() => {
                setView("custom");
                setPlaying(false);
              }}
            >
              <MessageSquare size={16} />
              Try a question
            </button>
          </nav>
        </div>
      </header>
      <div hidden={view !== "custom"}>
        <Sandbox status={status} onBusy={setCustomBusy} />
      </div>
      <div className="workspace" hidden={view !== "saved"}>
        <aside className="library" aria-label="Case library">
          <div className="library-heading">
            <h2>Case library</h2>
            <div className="library-tools">
              <span className="case-count">10 cases</span>
              <CaseLegend />
            </div>
          </div>
          <label className="search">
            <Search size={18} aria-hidden="true" />
            <input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Find a question"
              aria-label="Find a question"
            />
          </label>
          <div className="case-list">
            {catalogue?.groups.map((g) => {
              const cases = catalogue.cases.filter(
                (c) =>
                  c.group === g.id &&
                  `${c.question} ${c.db_id} ${c.question_id}`
                    .toLowerCase()
                    .includes(search.toLowerCase()),
              );
              if (!cases.length) return null;
              return (
                <section className="case-group" key={g.id}>
                  <h3>{g.label}</h3>
                  {cases.map((c) => (
                    <button
                      className={
                        "case-button " +
                        (caseId === c.question_id ? "selected" : "")
                      }
                      key={c.question_id}
                      disabled={isLiveBusy}
                      aria-current={
                        caseId === c.question_id ? "true" : undefined
                      }
                      onClick={() => setCaseId(c.question_id)}
                    >
                      <span>{c.question}</span>
                      <span className="case-meta">
                        <small
                          title={`#${c.question_id} · ${c.db_id.replaceAll("_", " ")}`}
                        >
                          #{c.question_id} <span>·</span>{" "}
                          {c.db_id.replaceAll("_", " ")}
                        </small>
                        <span className="case-outcomes">
                          {methods.map((m) => {
                            const matched = c.outcomes[m];
                            const label = `${outcomeLabels[m]}: ${matched ? "BIRD match" : "no BIRD match"}`;
                            return (
                              <span
                                key={m}
                                className={
                                  matched ? "text-positive" : "text-caution"
                                }
                                title={label}
                                data-method={m}
                                data-match={matched}
                              >
                                <span className="sr-only">{label}</span>
                                {matched ? (
                                  <Check size={14} aria-hidden="true" />
                                ) : (
                                  <X size={14} aria-hidden="true" />
                                )}
                              </span>
                            );
                          })}
                        </span>
                      </span>
                      {caseId === c.question_id && <ArrowRight size={15} />}
                    </button>
                  ))}
                </section>
              );
            })}
            {catalogue &&
              !catalogue.cases.some((c) =>
                `${c.question} ${c.db_id} ${c.question_id}`
                  .toLowerCase()
                  .includes(search.toLowerCase()),
              ) && (
                <p className="empty">No matching cases. Try a database name.</p>
              )}
          </div>
          <details className="library-note">
            <summary>
              <DisclosureTitle>How these cases were chosen</DisclosureTitle>
            </summary>
            <p>
              Two selected examples per outcome, not an accuracy sample.
              Full-run counts:
            </p>
            {catalogue?.groups.map((g) => (
              <div className="count-row" key={g.id}>
                <span>{g.label}</span>
                <b>{g.count}</b>
              </div>
            ))}
            <p>
              “Recovers” means an initially incorrect result later matches the
              BIRD gold result.
            </p>
          </details>
        </aside>
        <main id="main">
          <div className="mobile-picker">
            <label htmlFor="case-picker">Case</label>
            <div className="case-select">
              <select
                id="case-picker"
                value={caseId}
                disabled={isLiveBusy}
                onChange={(e) => setCaseId(Number(e.target.value))}
              >
                {catalogue?.groups.map((g) => (
                  <optgroup label={g.label} key={g.id}>
                    {catalogue.cases
                      .filter((c) => c.group === g.id)
                      .map((c) => (
                        <option key={c.question_id} value={c.question_id}>
                          #{c.question_id} · {c.question}
                        </option>
                      ))}
                  </optgroup>
                ))}
              </select>
              <ChevronDown size={16} aria-hidden="true" />
            </div>
          </div>
          {error && !branch && (
            <div className="error" role="alert">
              <Info size={18} />
              <span>{error}</span>
              <button aria-label="Dismiss error" onClick={() => setError("")}>
                <X size={16} />
              </button>
            </div>
          )}
          {!data ? (
            <div className="loading" role="status">
              <LoaderCircle className="spin" />
              Loading saved evidence…
            </div>
          ) : (
            <>
              <section className="question-header">
                <div className="eyebrow">
                  <span>
                    <Database size={14} />
                    {data.task.db_id.replaceAll("_", " ")}
                  </span>
                  <span>BIRD Dev · #{caseId}</span>
                  <span className="outcome-label">{selectedGroup?.label}</span>
                </div>
                <h1>{data.task.question}</h1>
                <div className="question-tools">
                  <button
                    className="btn-secondary"
                    onClick={() => {
                      setContext({
                        title: "What the SQL model receives",
                        value: {
                          role: "policy",
                          private: false,
                          prompt: data.schema,
                          description:
                            "The SQL model receives the question and CREATE TABLE definitions, without sampled values or BIRD evidence hints. Recovery adds previous rejected SQL and, for clarification, earlier questions and replies. Result previews in this app are for you; they were not supplied to the SQL model. The evaluator and simulator alone can read the gold SQL.",
                        },
                      });
                      setShowContext(true);
                    }}
                  >
                    <Code2 size={15} />
                    Model input & schema
                  </button>
                  <details className="case-detail">
                    <summary>
                      <DisclosureTitle>What this case shows</DisclosureTitle>
                    </summary>
                    <p>{data.note}</p>
                  </details>
                </div>
              </section>
              <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
                <h2 className="text-sm">Compare methods</h2>
                <span className="text-xs text-muted">Saved final outcomes</span>
              </div>
              <div
                className="method-grid"
                role="group"
                aria-label="Compare methods"
              >
                {methods.map((m) => {
                  const a = data.methods[m];
                  return (
                    <button
                      key={m}
                      aria-label={a.label}
                      aria-pressed={method === m}
                      disabled={isLiveBusy}
                      className={"method " + (method === m ? "active" : "")}
                      onClick={() => selectMethod(m)}
                    >
                      <div className="method-title">
                        <span>{a.label}</span>
                        {method === m && (
                          <CircleCheck
                            size={14}
                            className="selection-marker"
                            aria-hidden="true"
                          />
                        )}
                      </div>
                      <div
                        className="mini-flow"
                        aria-label={`${a.attempts} normal SQL attempts, ${a.turns} replies`}
                      >
                        {a.steps
                          .filter((s) => s.kind !== "answer")
                          .map((s) => (
                            <span
                              key={s.id}
                              role="img"
                              className={
                                s.kind === "question"
                                  ? "mini-question"
                                  : s.candidate?.official_ex
                                    ? "mini-match"
                                    : "mini-sql"
                              }
                              title={
                                s.kind === "question"
                                  ? "Clarification question"
                                  : s.candidate?.official_ex
                                    ? "BIRD result matched"
                                    : "BIRD result not matched"
                              }
                            >
                              {s.kind === "question" ? (
                                <MessageSquare size={14} aria-hidden="true" />
                              ) : s.candidate?.official_ex ? (
                                <Check size={14} aria-hidden="true" />
                              ) : (
                                <X size={14} aria-hidden="true" />
                              )}
                            </span>
                          ))}
                      </div>
                      <div className="method-footer">
                        <strong
                          className={a.success ? "text-success" : "text-miss"}
                        >
                          {a.success ? "Matched" : "Not matched"}
                        </strong>
                        <span>
                          {a.attempts} SQL · {a.turns}{" "}
                          {a.turns === 1 ? "reply" : "replies"}
                        </span>
                      </div>
                    </button>
                  );
                })}
              </div>
              <details className="method-guide">
                <summary>
                  <DisclosureTitle>How to read this comparison</DisclosureTitle>
                </summary>
                <div>
                  <p>
                    All three methods share the initial SQL and any immediate
                    repair. BIRD compares sets of result rows, ignoring row
                    order and duplicate rows. This score determines whether the
                    controller stops or tries again.
                  </p>
                  <dl className="space-y-2">
                    {methods.map((m) => (
                      <div key={m}>
                        <dt className="inline font-medium text-ink">
                          {data.methods[m].label}:{" "}
                        </dt>
                        <dd className="inline">
                          {data.methods[m].description}
                        </dd>
                      </div>
                    ))}
                  </dl>
                  <div
                    className="flow-legend"
                    aria-label="Trajectory diagram legend"
                  >
                    <span>
                      <X size={12} /> No BIRD match
                    </span>
                    <span>
                      <MessageSquare size={12} /> Clarification question
                    </span>
                    <span>
                      <Check size={12} /> BIRD match
                    </span>
                  </div>
                </div>
              </details>
              <section
                className={"replay " + (branch ? "live-replay" : "")}
                aria-label="Trajectory replay"
              >
                <div className="replay-heading">
                  <div>
                    <span className={"mode-label " + (branch ? "live" : "")}>
                      {branch ? (
                        <>
                          <GitBranch size={15} />
                          Live branch
                        </>
                      ) : (
                        <>
                          <FileText size={15} />
                          Replay
                        </>
                      )}
                    </span>
                    <h2>
                      {branch
                        ? "Your continuation"
                        : data.methods[method].label}
                    </h2>
                  </div>
                  {branch ? (
                    <button
                      className="btn-secondary"
                      disabled={!!busy}
                      onClick={() => {
                        setBranch(undefined);
                        setCursor(data.methods[method].steps.length);
                        setReply("");
                        setSuggestion("");
                        setError("");
                      }}
                    >
                      <ArrowLeft size={15} />
                      Back to saved replay
                    </button>
                  ) : (
                    <span className="free-label">No API calls</span>
                  )}
                </div>
                {branch ? (
                  <div className="branch-notice">
                    <span>
                      {branch.mode === "original"
                        ? "Original target. New SQL is checked against the BIRD gold result."
                        : "Changed intent. You judge the result; gold scoring is off."}
                    </span>
                    <small>
                      {branch.attempts}/5 SQL attempts · {branch.answers}/4
                      replies · {branch.calls}/12 live calls
                    </small>
                  </div>
                ) : (
                  <div className="playback">
                    <div className="playback-actions">
                      <button
                        aria-label="Restart replay"
                        onClick={() => {
                          setCursor(1);
                          setPlaying(false);
                        }}
                        disabled={cursor === 1 && !playing}
                      >
                        <RotateCcw size={16} />
                      </button>
                      <button
                        className="play-button"
                        aria-label={playing ? "Pause replay" : "Play replay"}
                        onClick={() => {
                          if (cursor >= steps.length) setCursor(1);
                          setPlaying(!playing);
                        }}
                        disabled={steps.length <= 1}
                      >
                        {playing ? <Pause size={15} /> : <Play size={15} />}
                        <span>{playing ? "Pause" : "Play"}</span>
                      </button>
                      <button
                        aria-label="Previous step"
                        disabled={cursor <= 1}
                        onClick={() => {
                          setPlaying(false);
                          setCursor((c) => c - 1);
                        }}
                      >
                        <ArrowLeft size={16} />
                      </button>
                      <button
                        className="next"
                        disabled={cursor >= steps.length}
                        onClick={() => {
                          setPlaying(false);
                          setCursor((c) => c + 1);
                        }}
                      >
                        Next
                        <ArrowRight size={15} />
                      </button>
                    </div>
                    <div className="playback-end">
                      <span>
                        {cursor} / {steps.length} steps
                      </span>
                      <button
                        className="btn-secondary"
                        disabled={cursor === steps.length}
                        onClick={() => {
                          setCursor(steps.length);
                          setPlaying(false);
                        }}
                      >
                        <ListEnd size={16} />
                        Show all
                      </button>
                    </div>
                  </div>
                )}
                <div className="timeline" aria-label="Visible trajectory steps">
                  {visible.map((s, i) => {
                    const pk = `${caseId}-${method}-${branch?.id || "saved"}-${s.id}`;
                    return (
                      <article
                        className={
                          "step " +
                          (s.kind === "sql" ? "sql-step" : s.kind) +
                          (s.source === "live" ? " live-step" : "")
                        }
                        key={`${branch?.id || "saved"}-${s.id}`}
                      >
                        <div
                          className="step-marker"
                          aria-label={`Step ${i + 1}`}
                        >
                          {i + 1}
                        </div>
                        <div className="step-body">
                          <div className="step-heading">
                            <div>
                              <h3>
                                {s.kind === "sql"
                                  ? i === 0
                                    ? "Generate the first SQL"
                                    : s.call_kind?.includes("invalid_repair")
                                      ? "Repair invalid SQL"
                                      : "Try another SQL"
                                  : s.kind === "question"
                                    ? "Ask for clarification"
                                    : s.source === "live"
                                      ? "Your reply"
                                      : "Simulated user replies"}
                              </h3>
                              {s.source === "live" && (
                                <span className="live-tag">Live</span>
                              )}
                            </div>
                            {s.kind === "sql" &&
                              (s.candidate!.status !== "ok" ? (
                                <span className="badge miss">
                                  Execution error
                                </span>
                              ) : (
                                <Badge match={s.candidate!.official_ex} />
                              ))}
                          </div>
                          {s.kind === "sql" ? (
                            <>
                              <Sql text={s.candidate!.sql} />
                              <SqlChange
                                before={
                                  visible
                                    .slice(0, i)
                                    .filter((step) => step.kind === "sql")
                                    .at(-1)?.candidate?.sql
                                }
                                after={s.candidate!.sql}
                              />
                              {s.source === "saved" && (
                                <details className="scoring-details">
                                  <summary>
                                    Scoring details{" "}
                                    <ChevronDown
                                      size={14}
                                      className="disclosure-chevron"
                                    />
                                  </summary>
                                  <p>
                                    BIRD:{" "}
                                    {s.candidate!.official_ex
                                      ? "match"
                                      : "no match"}
                                    . Sphinteract-compatible:{" "}
                                    {s.candidate!.sph_compatible_ex
                                      ? "match"
                                      : "no match"}
                                    .
                                  </p>
                                  <p>
                                    The second score checks this saved query
                                    only. BIRD controlled the recorded
                                    continuation.
                                  </p>
                                </details>
                              )}
                              {s.candidate!.error && (
                                <p className="inline-error">
                                  {s.candidate!.error}
                                </p>
                              )}
                              <div className="step-actions">
                                <button
                                  className="btn-secondary"
                                  onClick={() => preview(s)}
                                  disabled={!!busy}
                                >
                                  <Database size={14} />
                                  {busy === `preview-${s.id}`
                                    ? "Reading results…"
                                    : expandedPreview[pk]
                                      ? "Hide results"
                                      : "Results"}
                                </button>
                                <button
                                  className="btn-secondary"
                                  onClick={() => inspect(s)}
                                  disabled={!!busy}
                                >
                                  <FileText size={14} />
                                  Context
                                </button>
                                <span>
                                  {s.candidate!.row_count}{" "}
                                  {s.candidate!.row_count === 1
                                    ? "row"
                                    : "rows"}{" "}
                                </span>
                              </div>
                              {expandedPreview[pk] && previews[pk] && (
                                <PreviewTable data={previews[pk]} />
                              )}
                            </>
                          ) : s.kind === "question" ? (
                            <>
                              <p className="clarification-question">
                                {s.output.question}
                              </p>
                              <ol className="options" type="A">
                                {s.output.options?.map((o, index) => (
                                  <li key={index}>
                                    <span>
                                      {String.fromCharCode(65 + index)}
                                    </span>
                                    {branch?.phase === "reply" &&
                                    i === visible.length - 1 ? (
                                      <button
                                        className="option-select"
                                        disabled={!!busy}
                                        onClick={() =>
                                          setReply(
                                            `${String.fromCharCode(65 + index)}. ${o.replace(/^\s*[a-d][.)]\s*/i, "")}`,
                                          )
                                        }
                                      >
                                        {o.replace(/^\s*[a-d][.)]\s*/i, "")}
                                      </button>
                                    ) : (
                                      o.replace(/^\s*[a-d][.)]\s*/i, "")
                                    )}
                                  </li>
                                ))}
                              </ol>
                              <div className="step-actions">
                                <button
                                  className="btn-secondary"
                                  disabled={!!busy}
                                  onClick={() => inspect(s)}
                                >
                                  <Search size={14} />
                                  Review
                                </button>
                                {!branch && (
                                  <button
                                    className="fork-button"
                                    disabled={!!busy}
                                    onClick={() => {
                                      setPlaying(false);
                                      setForkStep(s.id);
                                      setMode("original");
                                      setError("");
                                    }}
                                  >
                                    <GitBranch size={14} />
                                    Try your reply
                                  </button>
                                )}
                              </div>
                            </>
                          ) : (
                            <>
                              <p className="answer-text">
                                {cleanAnswer(s.output.answer || "")}
                              </p>
                              <div className="step-actions">
                                {s.source === "saved" && (
                                  <button
                                    className="btn-secondary"
                                    disabled={!!busy}
                                    onClick={() => inspect(s)}
                                  >
                                    <FileText size={14} />
                                    Reply context
                                  </button>
                                )}
                              </div>
                            </>
                          )}
                        </div>
                      </article>
                    );
                  })}
                </div>
                {!branch && cursor === steps.length && (
                  <div className="terminal">
                    <ShieldCheck size={19} />
                    <div>
                      <strong>
                        {data.methods[method].success
                          ? "The result matched the BIRD target."
                          : "The run ended without a BIRD match."}
                      </strong>
                      <details>
                        <summary>
                          <DisclosureTitle>Run details</DisclosureTitle>
                        </summary>
                        <p>
                          {data.methods[method].termination.replaceAll(
                            "_",
                            " ",
                          )}{" "}
                          · {data.methods[method].turns} replies ·{" "}
                          {data.methods[method].attempts} normal SQL attempts
                        </p>
                        <p>
                          Sphinteract-compatible check of saved attempts:{" "}
                          {data.methods[method].sph_success
                            ? "at least one match"
                            : "no match"}
                          . This is a reporting-only score. BIRD determined when
                          this run stopped.
                        </p>
                      </details>
                    </div>
                  </div>
                )}
                {branch && (
                  <div className="live-controls">
                    {status?.live.branch_inputs_ready === false && (
                      <p className="small muted">
                        Install live inputs: from <code>experiment/</code>, run
                        {" "}<code>python -m readiness.fetch_public_inputs demo</code> and reload.
                      </p>
                    )}
                    {error && (
                      <p role="alert" className="error">
                        {error}
                      </p>
                    )}
                    <div className="live-control-heading">
                      <GitBranch size={18} />
                      <h3>
                        {branch.phase === "reply"
                          ? "Continue with your reply"
                          : branch.phase === "done"
                            ? "This branch has ended"
                            : "What happens next?"}
                      </h3>
                    </div>
                    {branch.phase === "reply" ? (
                      <>
                        <label htmlFor="reply">
                          Your answer to the last clarification
                        </label>
                        <textarea
                          id="reply"
                          value={reply}
                          onChange={(e) => setReply(e.target.value)}
                          maxLength={4000}
                          rows={3}
                          placeholder="Choose an option or describe what you mean…"
                          disabled={!!busy}
                        />
                        <div className="reply-tools">
                          {branch.steps.at(-1)?.source === "saved" && (
                            <button
                              className="btn-secondary"
                              disabled={!!busy}
                              onClick={() => {
                                const next =
                                  data.methods[method].steps[
                                    branch.fork_step + 1
                                  ];
                                if (next?.kind === "answer")
                                  setReply(next.output.answer || "");
                              }}
                            >
                              Use recorded reply · free
                            </button>
                          )}
                          <button
                            className="btn-secondary"
                            disabled={!!busy || !canUseLiveBranch}
                            onClick={() => act("suggest")}
                          >
                            <Sparkles size={14} />
                            {branch.mode === "original"
                              ? "Generate oracle reply"
                              : "Suggest a possible reply"}{" "}
                            · API
                          </button>
                        </div>
                        {suggestion && (
                          <div className="suggestion">
                            <p>{cleanAnswer(suggestion)}</p>
                            <button
                              onClick={() => setReply(suggestion)}
                              disabled={!!busy}
                            >
                              Use this suggestion
                            </button>
                            <small>
                              {branch.mode === "original"
                                ? "Generated using the original gold SQL."
                                : "One possibility, not a correct or gold-informed answer."}
                            </small>
                          </div>
                        )}
                        <button
                          className="primary"
                          onClick={() => act("reply", reply)}
                          disabled={
                            !!busy || !reply.trim() || !canUseLiveBranch
                          }
                        >
                          <Send size={15} />
                          Send reply & generate SQL · API
                        </button>
                      </>
                    ) : branch.phase === "done" ? (
                      <p>
                        {branch.mode === "original" &&
                        branch.steps.at(-1)?.candidate?.official_ex
                          ? "The live SQL matches the original BIRD target."
                          : "The branch was stopped or reached its attempt limit."}{" "}
                        The saved benchmark is unchanged.
                      </p>
                    ) : (
                      <>
                        {branch.mode === "changed" && (
                          <>
                            <label htmlFor="feedback">
                              What still needs changing?
                            </label>
                            <textarea
                              id="feedback"
                              rows={2}
                              maxLength={4000}
                              value={reply}
                              onChange={(e) => setReply(e.target.value)}
                              disabled={!!busy}
                              placeholder="Explain what the last SQL or result missed…"
                            />
                          </>
                        )}
                        <div className="live-buttons">
                          {branch.phase === "repair" && (
                            <button
                              className="primary"
                              disabled={!!busy || !canUseLiveBranch}
                              onClick={() => act("repair")}
                            >
                              Repair invalid SQL · API
                            </button>
                          )}
                          <button
                            className={
                              branch.phase === "repair" ? "" : "primary"
                            }
                            disabled={
                              !!busy ||
                              !canUseLiveBranch ||
                              branch.attempts >= 5 ||
                              branch.answers >= 4 ||
                              (branch.mode === "changed" && !reply.trim())
                            }
                            onClick={() => act("ask", reply)}
                          >
                            <MessageSquare size={15} />
                            Ask the next question · API
                          </button>
                          <button disabled={!!busy} onClick={() => act("stop")}>
                            Stop here
                          </button>
                        </div>
                      </>
                    )}
                    {isLiveBusy && (
                      <p role="status" className="busy">
                        <LoaderCircle size={16} className="spin" />
                        Waiting for one API response. There is no automatic
                        loop.
                      </p>
                    )}
                    <small>
                      Live exploration is separate from the evaluated results.
                      Each API button makes at most one model request.
                    </small>
                  </div>
                )}
              </section>
              {catalogue && <StudyDetails catalogue={catalogue} />}
              <details className="method-notes">
                <summary>
                  <Info size={16} />
                  Study setup & provenance
                  <ChevronDown
                    size={16}
                    className="disclosure-chevron"
                    aria-hidden="true"
                  />
                </summary>
                <div className="notes-grid">
                  <div>
                    <h3>What is being compared?</h3>
                    <p>
                      The Sphinteract adaptation shares the initial SQL and any
                      immediate repair. The initial-attempt baseline stops after
                      rejection. Recovery allows up to five normal SQL attempts,
                      with one invalid-SQL repair per attempt. Reactive
                      clarification can ask up to four questions.
                    </p>
                    <p>
                      The SQL model receives the question and schema, not the
                      gold SQL. The simulator knows the gold SQL and supplies a
                      target-aware reply. Hidden result comparison drives
                      rejection and stopping.
                    </p>
                  </div>
                  <div>
                    <h3>What this demo covers</h3>
                    <p>
                      Ten selected cases illustrate the primary study, with
                      optional live exploration. Full-run statistics describe
                      all 1,534 tasks. Live conversations stay separate from
                      thesis results.
                    </p>
                    <p>
                      Saved result previews are cached from read-only SQL execution.
                      Benchmark scores come from the recorded run.
                    </p>
                    <h3>Source record</h3>
                    <code className="source-path">
                      {data.source}/tasks/{String(caseId).padStart(4, "0")}.json
                    </code>
                    <small className="hash">SHA-256 {data.record_sha256}</small>
                  </div>
                </div>
              </details>
            </>
          )}
        </main>
      </div>
      <dialog
        ref={dialog}
        className="fork-dialog"
        aria-label="Try a different reply"
        onCancel={() => setForkStep(undefined)}
      >
        <div className="dialog-content">
          <div className="dialog-heading">
            <h2>Try a different reply</h2>
            <button
              aria-label="Close live branch setup"
              disabled={!!busy}
              onClick={() => setForkStep(undefined)}
            >
              <X size={18} />
            </button>
          </div>
          <p>
            Keep the saved history up to this question. Everything after it will
            be a new branch.
          </p>
          <fieldset>
            <legend>What does your reply change?</legend>
            <label
              className={"mode-choice " + (mode === "original" ? "chosen" : "")}
            >
              <input
                type="radio"
                name="mode"
                checked={mode === "original"}
                onChange={() => setMode("original")}
              />
              <span>
                <strong>The same original task</strong>
                <small>
                  Keep BIRD gold scoring and optional gold-conditioned replies.
                </small>
              </span>
            </label>
            <label
              className={"mode-choice " + (mode === "changed" ? "chosen" : "")}
            >
              <input
                type="radio"
                name="mode"
                checked={mode === "changed"}
                onChange={() => setMode("changed")}
              />
              <span>
                <strong>I may change the intended request</strong>
                <small>
                  You judge the result. No gold scoring or oracle replies.
                </small>
              </span>
            </label>
          </fieldset>
          <p className="small muted">
            {status?.live.available
              ? `${status.live.model} · ${status.live.reasoning}. API calls start only when you send or generate a reply.`
              : "No API key configured. You can inspect the branch, but live generation is unavailable."}
          </p>
          {status?.live.branch_inputs_ready === false && (
            <p className="small muted">
              For live replies, run <code>python -m readiness.fetch_public_inputs demo</code>
              {" "}from <code>experiment/</code>, then reload this page.
            </p>
          )}
          <button className="primary" disabled={!!busy} onClick={fork}>
            <GitBranch size={16} />
            Start a branch · free
          </button>
          {error && (
            <p className="inline-error" role="alert">
              {error}
            </p>
          )}
        </div>
      </dialog>
      <dialog
        ref={contextDialog}
        className="context-dialog"
        aria-label={context?.title || "Model context"}
        onCancel={() => setShowContext(false)}
      >
        <div className="dialog-content">
          <div className="dialog-heading">
            <h2>{context?.title}</h2>
            <button
              aria-label="Close context"
              onClick={() => setShowContext(false)}
            >
              <X size={18} />
            </button>
          </div>
          {context && (
            <>
              <p>{context.value.description}</p>
              {context.value.verified && (
                <span className="verified">
                  <ShieldCheck size={15} />
                  Saved prompt hash verified
                </span>
              )}
              {context.value.prompt && (
                <ReviewSummary value={context.value.output} />
              )}
              {context.value.prompt && (
                <details open={context.value.output === undefined}>
                  <summary>
                    <DisclosureTitle>
                      Input supplied to the model
                    </DisclosureTitle>
                  </summary>
                  <pre className="raw" tabIndex={0}>
                    {context.value.prompt}
                  </pre>
                </details>
              )}
              {context.value.instructions && (
                <details>
                  <summary>
                    <DisclosureTitle>System instructions</DisclosureTitle>
                  </summary>
                  <pre className="raw" tabIndex={0}>
                    {context.value.instructions}
                  </pre>
                </details>
              )}
              {context.value.output !== undefined && (
                <details>
                  <summary>
                    <DisclosureTitle>Exact structured output</DisclosureTitle>
                  </summary>
                  <pre className="raw" tabIndex={0}>
                    {JSON.stringify(context.value.output, null, 2)}
                  </pre>
                </details>
              )}
              {context.value.usage && (
                <details>
                  <summary>
                    <DisclosureTitle>Recorded usage</DisclosureTitle>
                  </summary>
                  <pre className="raw" tabIndex={0}>
                    {JSON.stringify(context.value.usage, null, 2)}
                  </pre>
                </details>
              )}
              {context.value.output_schema !== undefined && (
                <details>
                  <summary>
                    <DisclosureTitle>Required output format</DisclosureTitle>
                  </summary>
                  <pre className="raw" tabIndex={0}>
                    {JSON.stringify(context.value.output_schema, null, 2)}
                  </pre>
                </details>
              )}
              {context.value.source && (
                <details>
                  <summary>
                    <DisclosureTitle>Source file</DisclosureTitle>
                  </summary>
                  <p className="source-path p-3">{context.value.source}</p>
                </details>
              )}
            </>
          )}
        </div>
      </dialog>
    </>
  );
}
createRoot(document.getElementById("root")!).render(<App />);
