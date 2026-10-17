import { ChevronDown, FlaskConical } from "lucide-react";
import type { Catalogue, Method } from "./types";

export function StudyDetails({ catalogue }: { catalogue: Catalogue }) {
  const summary = catalogue.summary;
  return (
    <details className="study-details">
      <summary>
        <FlaskConical size={16} /> Study results & information flow{" "}
        <ChevronDown size={16} className="disclosure-chevron" />
      </summary>
      <div className="study-content">
        <section>
          <h3>What clarification adds to retry</h3>
          <p>
            All {catalogue.total_tasks.toLocaleString()} BIRD Dev tasks, under
            the study's simulated-user protocol. The case list contains selected
            examples.
          </p>
          <div className="study-bars">
            {(Object.keys(summary.methods) as Method[]).map((method) => {
              const m = summary.methods[method];
              return (
                <div key={method}>
                  <div className="bar-label">
                    <span>{m.label}</span>
                    <strong>{(m.ex * 100).toFixed(2)}%</strong>
                  </div>
                  <div className="bar-track">
                    <div
                      className={`bar-fill ${method}`}
                      style={{ width: `${m.ex * 100}%` }}
                    />
                  </div>
                  <small>
                    {m.successes.toLocaleString()} solved · {m.turns.toFixed(2)}{" "}
                    mean user turns
                  </small>
                </div>
              );
            })}
          </div>
          <p className="study-takeaway">
            Clarification adds{" "}
            <strong>{summary.difference.toFixed(2)} percentage points</strong>{" "}
            over SQL-only recovery. 95% confidence interval:{" "}
            {summary.interval.map((n) => n.toFixed(2)).join(" to ")} points.
          </p>
          <p>
            Of {summary.initial_failures} initial failures, retry recovers{" "}
            {summary.methods.sql_only_recovery.recovered} and clarification
            recovers {summary.methods.reactive_clarification.recovered}.
          </p>
        </section>
        <section>
          <h3>Who receives what</h3>
          <div className="info-flow">
            <div>
              <h4>SQL model</h4>
              <p>
                Question + table definitions. Later attempts also receive
                previous rejected SQL and any clarification answers.
              </p>
              <small>No sampled values, result rows or gold SQL.</small>
            </div>
            <div>
              <h4>Evaluator</h4>
              <p>
                Generated and gold query results. BIRD's row-set comparison
                determines acceptance and when the run stops.
              </p>
            </div>
            <div>
              <h4>Simulated user</h4>
              <p>
                Question, clarification and hidden gold SQL. Its answer goes
                back to the SQL model in the clarification method.
              </p>
            </div>
          </div>
          <p>
            After rejection, SQL-only recovery retries directly. Reactive
            clarification asks the simulated user, then retries.
          </p>
        </section>
        <section>
          <h3>Three studies in the thesis</h3>
          <dl className="study-list">
            <div>
              <dt>Clarification versus retry</dt>
              <dd>
                Same initial SQL and generation limits. This is the study
                replayed here.
              </dd>
            </div>
            <div>
              <dt>Five controller combinations</dt>
              <dd>
                Tests early questions, reactive questions and early stopping on
                BIRD Dev.
              </dd>
            </div>
            <div>
              <dt>Four Mini-Interact controllers</dt>
              <dd>
                Tests interaction policies in a different task environment with
                its own evaluator.
              </dd>
            </div>
          </dl>
          <p>
            <a
              href="https://github.com/ZhaoFuheng/Sphinteract"
              target="_blank"
              rel="noreferrer"
            >
              Sphinteract
            </a>{" "}
            already compares clarification with retry. This project adapts that
            method, measures task-level differences and extends the controller
            comparison.
          </p>
        </section>
        <details className="scoring-details">
          <summary>
            Scoring rules & source{" "}
            <ChevronDown size={14} className="disclosure-chevron" />
          </summary>
          <p>
            BIRD compares sets of result rows, ignoring row order and duplicate
            rows while retaining column positions. A match is a result match on
            this database.
          </p>
          <p>
            The Sphinteract-compatible score is a separate check of saved
            queries. It retains row count and handles column order differently.
            It did not control these trajectories; BIRD did.
          </p>
          <code className="source-path">{summary.source}</code>
        </details>
      </div>
    </details>
  );
}
