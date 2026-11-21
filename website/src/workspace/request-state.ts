/** One in-flight poll; forced refresh cancels its predecessor. */
export type RequestTicket = { id: number; controller: AbortController };
export class LatestRequest {
  private generation = 0;
  private current: RequestTicket | null = null;
  begin(force = false): RequestTicket | null {
    if (this.current && !force) return null;
    this.current?.controller.abort();
    const ticket = { id: ++this.generation, controller: new AbortController() };
    this.current = ticket;
    return ticket;
  }
  owns(ticket: RequestTicket) {
    return this.current?.id === ticket.id;
  }
  isCurrent(ticket: RequestTicket) {
    return this.current?.id === ticket.id && !ticket.controller.signal.aborted;
  }
  finish(ticket: RequestTicket) {
    if (this.current?.id === ticket.id) this.current = null;
  }
  cancel() {
    ++this.generation;
    this.current?.controller.abort();
    this.current = null;
  }
}

/** An authenticated but busy ledger is not a transport or worker failure. */
export function isSnapshotBusy(error: unknown, hasSnapshot: boolean) {
  return Boolean(
    hasSnapshot &&
    error &&
    typeof error === "object" &&
    (error as { status?: unknown }).status === 409 &&
    (error as { code?: unknown }).code === "conflict",
  );
}
