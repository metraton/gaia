/** Correlate identity delivery to a single unchanged tool invocation, never permission. */
export class ShellEnvDelivery {
  private readonly pending = new Map<string, {
    sessionID: string
    agent: string
    attestation: string
    cwd: string
    args: Record<string, unknown>
    snapshot: string
  }>()

  private key(sessionID: string, callID: string): string {
    return JSON.stringify([sessionID, callID])
  }

  /** Record only a bridge-confirmed identity after successful policy processing. */
  remember(input: {
    sessionID: string; callID: string; agent: string; attestation: string
    cwd: string; args: Record<string, unknown>
  }): void {
    if (!input.sessionID || !input.callID || !input.agent || !input.attestation || !input.cwd) {
      throw new Error("Gaia shell environment lacks authenticated correlation")
    }
    const key = this.key(input.sessionID, input.callID)
    if (this.pending.has(key)) throw new Error("Gaia shell environment already pending for this call")
    this.pending.set(key, { ...input, snapshot: JSON.stringify(input.args) })
  }

  /** Deliver once, refusing changed arguments, identity, attestation or directory. */
  take(input: { sessionID: string; callID: string; agent?: string; attestation?: string; cwd: string }): string {
    const key = this.key(input.sessionID, input.callID)
    const record = this.pending.get(key)
    this.pending.delete(key)
    if (!record || record.agent !== input.agent || record.attestation !== input.attestation
      || record.cwd !== input.cwd || record.snapshot !== JSON.stringify(record.args)) {
      throw new Error("Gaia shell environment correlation missing or changed")
    }
    return record.agent
  }

  /** Forget a finished or failed tool call. */
  forget(sessionID: string, callID: string): void {
    this.pending.delete(this.key(sessionID, callID))
  }

  /** Release identity-only state when a session closes or fails. */
  clearSession(sessionID: string): void {
    for (const [key, record] of this.pending) {
      if (record.sessionID === sessionID) this.pending.delete(key)
    }
  }

  /** Release identity-only state when the plugin instance is disposed. */
  clear(): void {
    this.pending.clear()
  }
}
