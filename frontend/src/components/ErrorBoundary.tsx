import { Component, type ErrorInfo, type ReactNode } from "react";

interface Props {
  children: ReactNode;
}
interface State {
  error: Error | null;
}

/** Catches render-time crashes so one broken component can't blank the whole
 *  console — shows a recoverable fallback instead of a white screen. */
export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    // Surface to the console (and any wired error tracker) for diagnosis.
    console.error("Console crashed:", error, info.componentStack);
  }

  private reset = () => this.setState({ error: null });

  render() {
    if (this.state.error) {
      return (
        <div className="errorboundary" role="alert">
          <h2 className="errorboundary__title">Something went wrong</h2>
          <p className="errorboundary__msg">
            The console hit an unexpected error. Your investigation history is safe.
          </p>
          <pre className="errorboundary__detail">{this.state.error.message}</pre>
          <button className="errorboundary__btn" type="button" onClick={this.reset}>
            Try again
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}
