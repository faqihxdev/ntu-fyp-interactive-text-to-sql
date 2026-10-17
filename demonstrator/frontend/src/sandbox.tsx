import { useEffect, useState } from "react";
import {
  ArrowUp,
  ChevronDown,
  Database,
  FileCode2,
  LoaderCircle,
  MessageCircle,
  Plus,
  Send,
  Square,
} from "lucide-react";
import { api } from "./api";
import { PreviewTable, Sql, SqlChange } from "./query-view";
import type { CustomSession, DatabaseInfo, Status } from "./types";

export function Sandbox({
  status,
  onBusy,
}: {
  status?: Status;
  onBusy: (busy: boolean) => void;
}) {
  const [databases, setDatabases] = useState<DatabaseInfo[]>([]);
  const [dbId, setDbId] = useState("superhero");
  const [detail, setDetail] = useState<DatabaseInfo>();
  const [question, setQuestion] = useState("");
  const [reply, setReply] = useState("");
  const [session, setSession] = useState<CustomSession>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    api<DatabaseInfo[]>("/databases")
      .then(setDatabases)
      .catch((e) => setError(String(e.message)));
  }, []);
  useEffect(() => {
    let active = true;
    setDetail(undefined);
    api<DatabaseInfo>(`/databases/${dbId}`)
      .then((d) => {
        if (active) setDetail(d);
      })
      .catch((e) => {
        if (active) setError(String(e.message));
      });
    return () => {
      active = false;
    };
  }, [dbId]);
  useEffect(() => {
    onBusy(busy);
  }, [busy, onBusy]);
  const canCall = !!status?.live.available && !busy;
  const selectDatabase = (id: string) => {
    setDbId(id);
    setQuestion("");
    setError("");
  };
  async function act(action: string, text = "") {
    if (busy || !status) return;
    setBusy(true);
    setError("");
    let current = session;
    try {
      if (!current) {
        current = await api<CustomSession>(
          "/custom",
          { db_id: dbId, question },
          status.token,
        );
        setSession(current);
      }
      const result = await api<{ session: CustomSession }>(
        `/branches/${current.id}/actions`,
        {
          action,
          text,
          revision: current.revision,
          request_id: crypto.randomUUID(),
        },
        status.token,
      );
      setSession(result.session);
      setReply("");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      if (current) {
        try {
          setSession(await api<CustomSession>(`/branches/${current.id}`));
        } catch {
          /* Keep the last known session. */
        }
      }
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="workspace sandbox">
      <aside className="library database-library" aria-label="Databases">
        <div className="library-heading">
          <h2>Databases</h2>
          <span className="case-count">{databases.length}</span>
        </div>
        <div className="database-list">
          {databases.map((db) => (
            <button
              key={db.id}
              className={`database-button ${dbId === db.id ? "selected" : ""}`}
              aria-pressed={dbId === db.id}
              disabled={!!session || busy}
              onClick={() => selectDatabase(db.id)}
            >
              <Database size={16} />
              <span>{db.label}</span>
            </button>
          ))}
        </div>
        <p className="database-hint">
          Choose a database, then ask about its contents.
        </p>
      </aside>
      <main id="custom-main">
        <div className="mobile-picker">
          <label htmlFor="database-select">Database</label>
          <div className="case-select">
            <select
              id="database-select"
              value={dbId}
              disabled={!!session || busy}
              onChange={(e) => selectDatabase(e.target.value)}
            >
              {databases.map((db) => (
                <option key={db.id} value={db.id}>
                  {db.label}
                </option>
              ))}
            </select>
            <ChevronDown size={16} />
          </div>
        </div>
        <header className="custom-heading">
          <div>
            <h1>{detail?.label || "Loading database…"}</h1>
            <p>{detail?.description}</p>
          </div>
          {session && (
            <button
              disabled={busy}
              onClick={() => {
                setSession(undefined);
                setReply("");
                setError("");
              }}
            >
              <Plus size={16} /> New question
            </button>
          )}
        </header>
        {detail && (
          <details className="schema-guide">
            <summary>
              <Database size={15} /> Inside this database{" "}
              <span>{detail.tables?.length} tables</span>
              <ChevronDown size={15} className="disclosure-chevron" />
            </summary>
            <div className="schema-content">
              <div className="table-names">
                {detail.tables?.map((table) => (
                  <code key={table}>{table}</code>
                ))}
              </div>
              <p>
                The model receives your question, these table definitions and
                the conversation. SQL errors are included for repairs. Result
                previews are for you; row values are not added to the model's
                context.
              </p>
              <pre
                className="raw"
                tabIndex={0}
                aria-label="Database table definitions"
              >
                {detail.schema}
              </pre>
            </div>
          </details>
        )}
        {error && (
          <p className="inline-error" role="alert">
            {error}
          </p>
        )}
        {!session ? (
          <>
            <section
              className="starter-section"
              aria-labelledby="starter-heading"
            >
              <div className="section-heading">
                <h2 id="starter-heading">Start with an example</h2>
                <span>Selecting one uses no API calls</span>
              </div>
              <div className="starter-questions">
                {detail?.examples.map((example) => (
                  <button
                    key={example}
                    onClick={() => setQuestion(example)}
                    disabled={busy}
                  >
                    <span>{example}</span>
                    <ArrowUp size={16} />
                  </button>
                ))}
              </div>
            </section>
            <form
              className="question-composer"
              onSubmit={(e) => {
                e.preventDefault();
                void act("generate");
              }}
            >
              <label htmlFor="custom-question">Or ask your own question</label>
              <textarea
                id="custom-question"
                rows={3}
                maxLength={4000}
                value={question}
                onChange={(e) => setQuestion(e.target.value)}
                placeholder="What would you like to find out?"
              />
              <div className="composer-footer">
                <span>The AI can ask you to clarify before writing SQL.</span>
                <button
                  className="primary"
                  disabled={!question.trim() || !canCall || !detail}
                  type="submit"
                >
                  <Send size={15} /> Ask AI · API
                </button>
              </div>
            </form>
          </>
        ) : (
          <section
            className="custom-conversation"
            aria-label="Custom conversation"
          >
            <div className="custom-request">
              <h2>{session.question}</h2>
              <span>
                {session.calls}/12 API calls · {session.attempts}/5 queries
              </span>
            </div>
            <p className="custom-boundary">
              You judge the answer. Successful execution means the SQL ran, not
              that it answered your question correctly.
            </p>
            <div className="timeline">
              {session.steps.map((step, index) => (
                <article
                  className={`step ${step.kind === "question" ? "question" : ""}`}
                  key={step.id}
                >
                  <div className="step-marker">
                    {step.kind === "sql" ? (
                      <FileCode2 size={14} />
                    ) : (
                      <MessageCircle size={14} />
                    )}
                  </div>
                  <div className="step-body">
                    <div className="step-heading">
                      <h3>
                        {step.kind === "sql"
                          ? step.call_kind === "custom_invalid_repair"
                            ? "SQL repair"
                            : "Generated SQL"
                          : step.kind === "question"
                            ? "Clarification"
                            : "Your reply"}
                      </h3>
                      {step.candidate && (
                        <span
                          className={`badge ${step.candidate.status === "ok" ? "neutral" : "miss"}`}
                        >
                          {step.candidate.status === "ok"
                            ? "Executed · unverified"
                            : "Execution error"}
                        </span>
                      )}
                    </div>
                    {step.kind === "sql" && (
                      <>
                        <Sql
                          text={step.candidate?.sql || step.output.sql || ""}
                        />
                        <SqlChange
                          before={
                            session.steps
                              .slice(0, index)
                              .filter((s) => s.kind === "sql")
                              .at(-1)?.candidate?.sql
                          }
                          after={step.candidate?.sql || ""}
                        />
                        {step.preview && <PreviewTable data={step.preview} />}
                      </>
                    )}
                    {step.kind === "question" && (
                      <>
                        <p className="clarification-question">
                          {step.output.question}
                        </p>
                        <ul className="options">
                          {step.output.options?.map((option, i) => (
                            <li key={i}>
                              {index === session.steps.length - 1 &&
                              session.phase === "reply" ? (
                                <button
                                  className="option-select"
                                  disabled={busy}
                                  onClick={() => setReply(option)}
                                >
                                  {option}
                                </button>
                              ) : (
                                option
                              )}
                            </li>
                          ))}
                        </ul>
                      </>
                    )}
                    {step.kind === "answer" && (
                      <p className="answer-text">{step.output.answer}</p>
                    )}
                    {step.context && (
                      <details className="scoring-details">
                        <summary>
                          Model input & output{" "}
                          <ChevronDown
                            size={14}
                            className="disclosure-chevron"
                          />
                        </summary>
                        <p>
                          Actual request sent for this step, not the model's
                          private reasoning.
                        </p>
                        <h4>Input</h4>
                        <pre className="raw" tabIndex={0}>
                          {step.context.prompt}
                        </pre>
                        <h4>Instructions</h4>
                        <pre className="raw" tabIndex={0}>
                          {step.context.instructions}
                        </pre>
                        <h4>Output</h4>
                        <pre className="raw" tabIndex={0}>
                          {JSON.stringify(step.context.output, null, 2)}
                        </pre>
                        <details>
                          <summary>
                            Response format & model{" "}
                            <ChevronDown
                              size={14}
                              className="disclosure-chevron"
                            />
                          </summary>
                          <p>
                            {step.context.model ||
                              "Model identifier unavailable"}
                          </p>
                          <pre className="raw" tabIndex={0}>
                            {JSON.stringify(
                              step.context.output_schema,
                              null,
                              2,
                            )}
                          </pre>
                        </details>
                      </details>
                    )}
                  </div>
                </article>
              ))}
            </div>
            {session.phase !== "done" && (
              <div className="live-controls">
                {(session.phase === "reply" || session.phase === "result") && (
                  <form
                    onSubmit={(e) => {
                      e.preventDefault();
                      void act(
                        session.phase === "reply" ? "reply" : "feedback",
                        reply,
                      );
                    }}
                  >
                    <label htmlFor="custom-reply">
                      {session.phase === "reply"
                        ? "Your clarification answer"
                        : "What would you like to change?"}
                    </label>
                    <textarea
                      id="custom-reply"
                      rows={2}
                      maxLength={4000}
                      value={reply}
                      onChange={(e) => setReply(e.target.value)}
                      disabled={busy}
                    />
                    <div className="live-buttons">
                      <button
                        type="submit"
                        className="primary"
                        disabled={!reply.trim() || !canCall}
                      >
                        <Send size={15} />
                        {session.phase === "reply"
                          ? "Send answer · API"
                          : "Request revision · API"}
                      </button>
                      <button
                        type="button"
                        disabled={busy}
                        onClick={() => void act("stop")}
                      >
                        <Square size={14} /> Finish
                      </button>
                    </div>
                  </form>
                )}
                {session.phase === "ready" && (
                  <button
                    className="primary"
                    disabled={!canCall}
                    onClick={() => void act("generate")}
                  >
                    <Send size={15} /> Generate response · API
                  </button>
                )}
                {session.phase === "repair" && (
                  <>
                    <p>
                      The query could not run. You can request one repair for
                      this attempt.
                    </p>
                    <div className="live-buttons">
                      <button
                        className="primary"
                        disabled={!canCall}
                        onClick={() => void act("repair")}
                      >
                        Repair SQL · API
                      </button>
                      <button disabled={busy} onClick={() => void act("stop")}>
                        <Square size={14} /> Finish
                      </button>
                    </div>
                  </>
                )}
                {busy && (
                  <p role="status" className="busy">
                    <LoaderCircle size={16} className="spin" /> Waiting for the
                    API…
                  </p>
                )}
              </div>
            )}
            {session.phase === "done" && (
              <p className="custom-ended">
                Conversation ended. Use “New question” to start again.
              </p>
            )}
          </section>
        )}
        <p className="custom-footnote">
          {status?.live.available
            ? `Live responses use ${status.live.model} / ${status.live.reasoning} and your API credits.`
            : "Live responses are unavailable. Configure the API key and restart the server to try a question."}{" "}
          Custom exploration stays separate from the recorded study results.
        </p>
      </main>
    </div>
  );
}
