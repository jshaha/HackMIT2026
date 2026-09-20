import { useMemo, useState } from "react"
import { motion } from "motion/react"
import type { CrfField, CrfForm, EdcForms } from "@/lib/types"

const ease = [0.16, 1, 0.3, 1] as const

/** Stable key for a form so the clinician's e-sign persists across polls even
 *  as the backend rewrites edc_forms.json. Keyed by subject+visit+form. */
function formKey(edc: EdcForms, form: CrfForm): string {
  return `${form.subject ?? edc.subject}|${form.visit ?? edc.visit}|${form.form}`
}

/** Effective status of a field after applying a local clinician e-sign: a
 *  locally-signed form promotes its DRAFT fields to "confirmed". Empty fields
 *  are never confirmed by the button (nothing to sign). */
function effectiveStatus(f: CrfField, signed: boolean): CrfField["status"] {
  if (signed && f.status === "draft") return "confirmed"
  return f.status
}

function fmtValue(f: CrfField): string {
  if (f.value == null || f.value === "") return "—"
  if (typeof f.value === "boolean") return f.value ? "Yes" : "No"
  return String(f.value)
}

/** eCRF (Veeva-shaped) forms with ALCOA-C auto-fill. Auto-filled DRAFT fields
 *  are highlighted amber with provenance; a per-form Confirm / e-sign control
 *  marks that form's draft fields confirmed in LOCAL state (persisted across
 *  polls). This is the clinician-confirm step — nothing auto-confirms. */
export function EcrfPanel({ edc }: { edc: EdcForms }) {
  // Local, poll-persistent e-sign decisions keyed by subject+visit+form.
  const [signed, setSigned] = useState<Record<string, boolean>>({})

  const forms = edc.forms ?? []

  const totalDrafts = useMemo(() => {
    let n = 0
    for (const form of edc.forms ?? []) {
      const key = formKey(edc, form)
      if (signed[key]) continue
      for (const f of form.fields ?? []) {
        if (f.status === "draft") n++
      }
    }
    return n
  }, [edc, signed])

  return (
    <motion.section
      initial={{ opacity: 0, y: 16 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.5, ease }}
      className="card mt-4 p-7"
    >
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-[15px] font-semibold text-[color:var(--color-ink)]">
          eCRF · electronic case report forms
        </h2>
        <span className="font-mono text-[12px] text-[color:var(--color-ink-mute)]">
          {edc.study} · {edc.subject} · {edc.visit}
        </span>
      </div>

      <div
        className={`mt-2 inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-[11px] font-medium ${
          totalDrafts > 0 ? "bg-amber-100 text-amber-800" : "bg-emerald-50 text-emerald-700"
        }`}
      >
        {totalDrafts > 0 ? `${totalDrafts} drafts pending sign-off` : "auto-filled & e-signed"}
      </div>

      <div className="mt-4 flex flex-col gap-4">
        {forms.map((form) => {
          const key = formKey(edc, form)
          const isSigned = !!signed[key]
          const pendingDrafts = (form.fields ?? []).filter(
            (f) => !isSigned && f.status === "draft",
          ).length
          return (
            <FormCard
              key={key}
              form={form}
              isSigned={isSigned}
              pendingDrafts={pendingDrafts}
              onSign={() => setSigned((prev) => ({ ...prev, [key]: true }))}
            />
          )
        })}
      </div>
    </motion.section>
  )
}

function FormCard({
  form,
  isSigned,
  pendingDrafts,
  onSign,
}: {
  form: CrfForm
  isSigned: boolean
  pendingDrafts: number
  onSign: () => void
}) {
  return (
    <div className="rounded-md border border-[color:var(--color-hair)] bg-[color:var(--color-card-2)] p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <span className="rounded bg-[color:var(--color-card)] px-1.5 py-0.5 font-mono text-[11px] text-[color:var(--color-ink-mute)]">
            {form.form}
          </span>
          <span className="text-[14px] font-semibold text-[color:var(--color-ink)]">{form.name}</span>
        </div>
        {pendingDrafts > 0 ? (
          <button
            onClick={onSign}
            className="cursor-pointer rounded-md border border-emerald-400 bg-emerald-500 px-2.5 py-1 text-[12px] font-medium text-white transition hover:bg-emerald-600"
          >
            Confirm / e-sign ({pendingDrafts})
          </button>
        ) : (
          <span className="rounded-full bg-emerald-50 px-2.5 py-0.5 text-[11px] font-medium text-emerald-700">
            {(form.fields ?? []).some((f) => f.status === "confirmed") || isSigned ? "✓ e-signed" : "empty"}
          </span>
        )}
      </div>

      <ul className="mt-3 flex flex-col gap-1.5">
        {(form.fields ?? []).map((f) => (
          <FieldRow key={f.id} field={f} status={effectiveStatus(f, isSigned)} />
        ))}
      </ul>
    </div>
  )
}

function FieldRow({ field: f, status }: { field: CrfField; status: CrfField["status"] }) {
  const isDraft = status === "draft"
  const isConfirmed = status === "confirmed"
  const isEmptyRequired = status === "empty" && !!f.required

  return (
    <li
      className={`flex items-start justify-between gap-3 rounded-md px-2.5 py-1.5 ${
        isDraft
          ? "border border-amber-200 bg-amber-50"
          : isEmptyRequired
            ? "border border-dashed border-rose-200 bg-rose-50/40"
            : ""
      }`}
    >
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-1.5 text-[13px] text-[color:var(--color-ink-body)]">
          {isConfirmed && <span className="text-emerald-600">✓</span>}
          <span className={isEmptyRequired ? "text-rose-700" : ""}>{f.label}</span>
          {isEmptyRequired && (
            <span className="rounded-full bg-rose-100 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-rose-700">
              needs entry
            </span>
          )}
        </div>
        {isDraft && (f.source || f.confidence != null || f.captured_at) && (
          <div className="mt-0.5 font-mono text-[11px] text-amber-700">
            {f.source ?? "auto"}
            {f.confidence != null && ` · conf ${(f.confidence * 100).toFixed(0)}%`}
            {f.captured_at && ` · ${new Date(f.captured_at).toLocaleTimeString()}`}
          </div>
        )}
        {isConfirmed && f.signer && (
          <div className="mt-0.5 font-mono text-[11px] text-emerald-700">
            e-signed by {f.signer}
            {f.signed_at && ` · ${new Date(f.signed_at).toLocaleTimeString()}`}
          </div>
        )}
      </div>
      <div
        className={`shrink-0 text-right font-mono text-[13px] tabular-nums ${
          f.value == null || f.value === ""
            ? "text-[color:var(--color-ink-mute)]"
            : "text-[color:var(--color-ink)]"
        }`}
      >
        {fmtValue(f)}
        {isDraft && (
          <span className="ml-1.5 rounded-full bg-amber-200 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-amber-800">
            draft
          </span>
        )}
      </div>
    </li>
  )
}
