import hljs from "highlight.js/lib/core";
import sql from "highlight.js/lib/languages/sql";
import { ChevronDown } from "lucide-react";
import type { Preview } from "./types";

hljs.registerLanguage("sql", sql);

export function Sql({ text }: { text: string }) {
  return (
    <div className="sql-frame">
      <pre className="sql" tabIndex={0} aria-label="SQL query">
        <code
          dangerouslySetInnerHTML={{
            __html: hljs.highlight(text, { language: "sql" }).value,
          }}
        />
      </pre>
    </div>
  );
}

export function SqlChange({
  before,
  after,
}: {
  before?: string;
  after: string;
}) {
  if (!before) return null;
  if (before.trim() === after.trim())
    return <p className="sql-repeat">Same SQL as the previous attempt.</p>;
  // Compare whole lines, preserving literal values and case. Full source is always shown above.
  const oldLines = before.split("\n");
  const newLines = after.split("\n");
  const removed = oldLines.filter((line) => !newLines.includes(line));
  const added = newLines.filter((line) => !oldLines.includes(line));
  return (
    <details className="sql-change">
      <summary>
        SQL changes <ChevronDown size={14} className="disclosure-chevron" />
      </summary>
      <pre tabIndex={0} aria-label="Changed SQL lines">
        {removed.map((line, i) => (
          <span className="removed" key={`r${i}`}>
            − {line}
            {"\n"}
          </span>
        ))}
        {added.map((line, i) => (
          <span className="added" key={`a${i}`}>
            + {line}
            {"\n"}
          </span>
        ))}
      </pre>
      {!removed.length && !added.length && (
        <p>Line order or repetition changed. Compare the full queries above.</p>
      )}
    </details>
  );
}

export function PreviewTable({ data }: { data: Preview }) {
  return (
    <div className="preview">
      {data.columns.length > 0 && (
        <div
          className="table-scroll"
          tabIndex={0}
          role="region"
          aria-label="Query result rows"
        >
          <table>
            <thead>
              <tr>
                {data.columns.map((c, i) => (
                  <th key={i}>{c}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {data.preview.map((r, i) => (
                <tr key={i}>
                  {r.map((c, j) => (
                    <td key={j}>
                      {c === null ? (
                        <em>NULL</em>
                      ) : typeof c === "object" ? (
                        JSON.stringify(c)
                      ) : (
                        String(c)
                      )}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <p>
        {data.error ||
          `${data.row_count.toLocaleString()} ${data.row_count === 1 ? "row" : "rows"}${data.truncated ? " (row limit reached)" : ""}. Showing up to 8.`}
      </p>
      {data.source && (
        <details>
          <summary>
            Preview source <ChevronDown size={14} />
          </summary>
          <p>{data.source}</p>
        </details>
      )}
    </div>
  );
}
