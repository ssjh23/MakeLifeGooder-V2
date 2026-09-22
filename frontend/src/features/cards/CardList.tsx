/**
 * Screen 02b: the card grid, plus the "new card" form.
 */

import { useState } from "react";
import { Link } from "react-router-dom";
import { ApiError } from "../../api/client";
import type { CardCreate } from "../../api/types";
import { useCards, useCreateCard } from "./queries";

const LAST4_PATTERN = /^\d{4}$/;
const COLOUR_PATTERN = /^#[0-9A-Fa-f]{6}$/;

export function CardList() {
  const cards = useCards();
  const [showForm, setShowForm] = useState(false);

  return (
    <section className="screen">
      <div className="row">
        <h1>Cards</h1>
        <button type="button" className="button--primary" onClick={() => setShowForm((v) => !v)}>
          {showForm ? "Cancel" : "New card"}
        </button>
      </div>
      <p className="muted">
        A card is a label: a nickname, an institution and the last four digits.
        Nothing else about it is ever stored here. Click a card to rename it,
        recolour it, or archive it.
      </p>

      {showForm && <CreateCardForm onDone={() => setShowForm(false)} />}

      {cards.isLoading && <p className="muted">Loading cards…</p>}
      {cards.error && (
        <p className="error" role="alert">
          {cards.error instanceof ApiError ? cards.error.message : "Could not load cards."}
        </p>
      )}

      {cards.data && cards.data.length === 0 && !showForm && (
        <p className="muted">No cards yet. Add one before importing a statement.</p>
      )}

      {cards.data && cards.data.length > 0 && (
        <div className="grid">
          {cards.data.map((card) => (
            <Link
              key={card.id}
              to={card.id}
              className={`card-tile${card.archived ? " card-tile--archived" : ""}`}
              style={{ borderTopColor: card.colour ?? undefined, textDecoration: "none", color: "inherit" }}
            >
              <div className="row">
                <strong>{card.nickname}</strong>
                <span className="badge badge--muted">{card.status}</span>
              </div>
              <p className="muted">{card.institution}</p>
              <p>•••• {card.last4}</p>
              {card.statement_day && (
                <p className="muted">Statement day {card.statement_day}</p>
              )}
              <p className="muted">
                {card.statement_count} statement{card.statement_count === 1 ? "" : "s"}
              </p>
              <p className="card-tile-edit-hint">Edit details →</p>
            </Link>
          ))}
        </div>
      )}
    </section>
  );
}

function CreateCardForm({ onDone }: { onDone: () => void }) {
  const create = useCreateCard();
  const [nickname, setNickname] = useState("");
  const [institution, setInstitution] = useState("");
  const [type, setType] = useState<CardCreate["type"]>("credit");
  const [last4, setLast4] = useState("");
  const [statementDay, setStatementDay] = useState("");
  const [colour, setColour] = useState("#3c7fc0");
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    setError(null);

    if (!LAST4_PATTERN.test(last4)) {
      setError("Last 4 digits must be exactly four digits.");
      return;
    }
    if (colour && !COLOUR_PATTERN.test(colour)) {
      setError("Colour must be a hex code like #3c7fc0.");
      return;
    }

    try {
      await create.mutateAsync({
        nickname,
        institution,
        type,
        last4,
        statement_day: statementDay ? Number(statementDay) : null,
        currency: "SGD",
        colour: colour || null,
      });
      onDone();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not create the card.");
    }
  }

  return (
    <form className="form panel" onSubmit={handleSubmit}>
      <div className="field-row">
        <div className="field">
          <label htmlFor="nickname">Nickname</label>
          <input
            id="nickname"
            required
            maxLength={100}
            value={nickname}
            onChange={(event) => setNickname(event.target.value)}
          />
        </div>
        <div className="field">
          <label htmlFor="institution">Institution</label>
          <input
            id="institution"
            required
            maxLength={100}
            value={institution}
            onChange={(event) => setInstitution(event.target.value)}
          />
        </div>
      </div>

      <div className="field-row">
        <div className="field">
          <label htmlFor="type">Type</label>
          <select
            id="type"
            value={type}
            onChange={(event) => setType(event.target.value as CardCreate["type"])}
          >
            <option value="credit">Credit</option>
            <option value="current">Current</option>
            <option value="savings">Savings</option>
          </select>
        </div>
        <div className="field">
          <label htmlFor="last4">Last 4 digits</label>
          <input
            id="last4"
            required
            inputMode="numeric"
            maxLength={4}
            pattern="\d{4}"
            value={last4}
            onChange={(event) => setLast4(event.target.value.replace(/\D/g, "").slice(0, 4))}
          />
        </div>
      </div>

      <div className="field-row">
        <div className="field">
          <label htmlFor="statement-day">Statement day (optional)</label>
          <input
            id="statement-day"
            type="number"
            min={1}
            max={28}
            value={statementDay}
            onChange={(event) => setStatementDay(event.target.value)}
          />
        </div>
        <div className="field">
          <label htmlFor="colour">Colour</label>
          <input
            id="colour"
            type="color"
            value={colour}
            onChange={(event) => setColour(event.target.value)}
          />
        </div>
      </div>

      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}

      <div className="button-row">
        <button type="submit" className="button--primary" disabled={create.isPending}>
          {create.isPending ? "Adding…" : "Add card"}
        </button>
      </div>
    </form>
  );
}
