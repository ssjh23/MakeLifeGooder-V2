/**
 * Placeholder for the twenty-six screens that are routed but not built.
 *
 * Each names itself, its screen number and the endpoints behind it, so the
 * route map is legible from the running app rather than only from Notion. A
 * blank page would say nothing; this says what belongs here and where to look
 * when building it.
 */

interface Props {
  screen: string;
  title: string;
  endpoints: readonly string[];
  note?: string;
}

export function Placeholder({ screen, title, endpoints, note }: Props) {
  return (
    <section className="screen">
      <p className="muted">Screen {screen}</p>
      <h1>{title}</h1>
      {note && <p className="muted">{note}</p>}
      <h2>Endpoints</h2>
      <ul className="endpoints">
        {endpoints.map((endpoint) => (
          <li key={endpoint}>
            <code>{endpoint}</code>
          </li>
        ))}
      </ul>
    </section>
  );
}

/**
 * The screen map from User Flows, as data.
 *
 * Every screen has endpoints and every endpoint traces to a screen. Keeping the
 * mapping here makes a gap in either direction visible.
 */
export const SCREENS = {
  signIn: {
    screen: "00, 01",
    title: "Create account and sign in",
    endpoints: ["POST /auth/register", "POST /auth/login", "GET /me"],
  },
  firstImport: {
    screen: "02",
    title: "First import",
    endpoints: ["GET /me"],
    note: "Review, Dashboard and Rules render inert here, not hidden.",
  },
  cards: {
    screen: "02b, 02c, 02d",
    title: "Cards",
    endpoints: [
      "GET /cards",
      "POST /cards",
      "PATCH /cards/{id}",
      "GET /cards/{id}/archive-preview",
      "POST /cards/{id}/archive",
    ],
    note: "Cards are labels. No full number, expiry, CVV or bank login is ever accepted.",
  },
  review: {
    screen: "04, 04b, 04c, 05",
    title: "Review and classify",
    endpoints: [
      "GET /statements/{id}/review",
      "POST /review/merchants/{key}/classify",
      "POST /statements/{id}/review/finish",
      "GET /statements/{id}/review/duplicates",
    ],
    note: "Grouped by merchant. One decision per merchant, not per transaction.",
  },
  dashboard: {
    screen: "06, 06b, 06c",
    title: "Dashboard",
    endpoints: [
      "GET /dashboard",
      "GET /dashboard/categories/{id}",
      "GET /transactions/{id}",
      "POST /transactions/{id}/override",
    ],
    note: "Totals stay locked while anything is unclassified.",
  },
  rules: {
    screen: "07 to 07e",
    title: "Rules",
    endpoints: [
      "GET /rules",
      "POST /rules/preview",
      "POST /rules",
      "GET /rules/conflicts",
      "POST /rules/reapply/preview",
      "POST /rules/reapply",
    ],
    note: "Preview and rerun are separate. A rerun can overwrite decisions made by hand.",
  },
  account: {
    screen: "08, 08b, 08c",
    title: "Account",
    endpoints: ["GET /account", "POST /exports", "DELETE /account"],
  },
} as const;
