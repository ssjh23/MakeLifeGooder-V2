/**
 * Error boundary that surfaces the request id.
 *
 * Puts a trace pointer in front of the user, so a bug report carries its own
 * correlation id and nobody has to reconstruct "roughly when, roughly what" from
 * memory. That is the user-facing half of ADR-014: the backend returns
 * `X-Request-ID` on every response, and this is where it stops being an
 * implementation detail.
 */

import { Component, type ErrorInfo, type ReactNode } from "react";
import { ApiError } from "../api/client";

interface Props {
  children: ReactNode;
}

interface State {
  error: Error | null;
}

export class RequestIdBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    // Console only. Shipping this to a third party would send whatever the
    // component was rendering with it, and on these screens that is a person's
    // transactions.
    console.error("Unhandled error", error, info.componentStack);
  }

  render(): ReactNode {
    const { error } = this.state;
    if (!error) return this.props.children;

    const requestId = error instanceof ApiError ? error.requestId : null;
    const notWritten = error instanceof ApiError && error.isNotWritten;

    return (
      <div className="error-panel" role="alert">
        <h2>{notWritten ? "Not built yet" : "Something went wrong"}</h2>
        <p>{error.message}</p>
        {notWritten && (
          <p className="muted">
            This endpoint exists but its logic has not been written. That is the
            expected state of the scaffold.
          </p>
        )}
        {requestId && (
          <p className="muted">
            Quote this if you report it: <code>{requestId}</code>
          </p>
        )}
        <button type="button" onClick={() => this.setState({ error: null })}>
          Try again
        </button>
      </div>
    );
  }
}
