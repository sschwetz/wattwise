import { type FormEvent, useState } from "react";
import QRCode from "qrcode";
import { useChangePassword, useConfirmTotpReenroll, useMe, useRequestEmailChange, useStartTotpReenroll, useUpdateAmberApiKey, useUpdateDisplayName } from "../account/useAccount";
import { errorMessage } from "../api/client";

const cardClass = "space-y-4 rounded-xl border border-line bg-surface p-5";
const errorClass = "rounded-md border border-danger/30 bg-danger-soft px-3 py-2 text-sm text-danger";
const successClass = "text-sm text-flow";
const primaryButtonClass =
  "rounded-md bg-solar px-3 py-2 text-sm font-medium text-[#1a1203] hover:bg-solar/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-solar/60 disabled:opacity-50";

interface LabeledInputProps {
  label: string;
  ariaLabel: string;
  value: string;
  onChange: (value: string) => void;
  type?: string;
  placeholder?: string;
  autoComplete?: string;
}

/** The one label+input shape every field in the three security sections uses.
 * The visible label and the aria-label are separate props because they
 * deliberately differ in places: the aria-labels were made unique across
 * sections so getByLabelText can address a single field. */
function LabeledInput({ label, ariaLabel, value, onChange, type, placeholder, autoComplete }: LabeledInputProps) {
  return (
    <label className="flex flex-col text-sm text-ink-muted">
      {label}
      <input
        aria-label={ariaLabel}
        type={type}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        autoComplete={autoComplete}
        spellCheck={false}
        className="rounded-md border border-line bg-surface px-3 py-2 text-ink placeholder:text-ink-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-solar/60"
        required
      />
    </label>
  );
}

function ProfileSection() {
  const { data: me } = useMe();
  const [displayName, setDisplayName] = useState("");
  const updateDisplayName = useUpdateDisplayName();

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    updateDisplayName.mutate(displayName, {
      onSuccess: () => setDisplayName(""),
    });
  }

  return (
    <section className={cardClass}>
      <h2 className="font-display text-lg font-semibold text-ink">Profile</h2>
      {me && <p className="text-sm text-ink-muted">Current name: {me.display_name ?? "Not set"}</p>}
      {updateDisplayName.isError && (
        <p role="alert" className={errorClass}>
          {updateDisplayName.error instanceof Error
            ? updateDisplayName.error.message
            : "Failed to save your name. Please try again."}
        </p>
      )}
      {updateDisplayName.isSuccess && <p className={successClass}>Saved.</p>}
      <form onSubmit={handleSubmit} className="flex items-end gap-2">
        <label className="flex flex-col text-sm text-ink-muted">
          Display name
          <input
            aria-label="Display name"
            value={displayName}
            onChange={(e) => setDisplayName(e.target.value)}
            placeholder="Your name"
            autoComplete="name"
            spellCheck={false}
            className="rounded-md border border-line bg-surface px-3 py-2 text-ink placeholder:text-ink-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-solar/60"
            required
          />
        </label>
        <button type="submit" disabled={updateDisplayName.isPending} className={primaryButtonClass}>
          Save
        </button>
      </form>
    </section>
  );
}

function ChangePasswordSection() {
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [totpCode, setTotpCode] = useState("");
  const [mismatchError, setMismatchError] = useState<string | null>(null);
  const changePassword = useChangePassword();

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setMismatchError(null);
    if (newPassword !== confirmPassword) {
      setMismatchError("New password and confirmation do not match.");
      return;
    }
    changePassword.mutate(
      { current_password: currentPassword, new_password: newPassword, totp_code: totpCode },
      {
        onSuccess: () => {
          setCurrentPassword("");
          setNewPassword("");
          setConfirmPassword("");
          setTotpCode("");
        },
      },
    );
  }

  const apiError =
    changePassword.isError && changePassword.error instanceof Error
      ? changePassword.error.message
      : changePassword.isError
        ? "Failed to change password. Please try again."
        : null;
  const error = mismatchError ?? apiError;

  return (
    <section className={cardClass}>
      <h2 className="font-display text-lg font-semibold text-ink">Change password</h2>
      {error && (
        <p role="alert" className={errorClass}>
          {error}
        </p>
      )}
      {changePassword.isSuccess && <p className={successClass}>Password changed.</p>}
      <form onSubmit={handleSubmit} className="space-y-2">
        <LabeledInput
          label="Current password"
          ariaLabel="Current password"
          type="password"
          value={currentPassword}
          onChange={setCurrentPassword}
          autoComplete="current-password"
        />
        <LabeledInput
          label="New password"
          ariaLabel="New password"
          type="password"
          value={newPassword}
          onChange={setNewPassword}
          autoComplete="new-password"
        />
        <LabeledInput
          label="Confirm new password"
          ariaLabel="Confirm new password"
          type="password"
          value={confirmPassword}
          onChange={setConfirmPassword}
          autoComplete="new-password"
        />
        <LabeledInput
          label="Authenticator code"
          ariaLabel="Authenticator code"
          value={totpCode}
          onChange={setTotpCode}
          placeholder="6-digit code"
          autoComplete="one-time-code"
        />
        <button type="submit" disabled={changePassword.isPending} className={primaryButtonClass}>
          Change password
        </button>
      </form>
    </section>
  );
}

function ChangeEmailSection() {
  const { data: me } = useMe();
  const [newEmail, setNewEmail] = useState("");
  const [currentPassword, setCurrentPassword] = useState("");
  const [totpCode, setTotpCode] = useState("");
  const requestEmailChange = useRequestEmailChange();

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    requestEmailChange.mutate(
      { new_email: newEmail, current_password: currentPassword, totp_code: totpCode },
      {
        onSuccess: () => {
          setNewEmail("");
          setCurrentPassword("");
          setTotpCode("");
        },
      },
    );
  }

  return (
    <section className={cardClass}>
      <h2 className="font-display text-lg font-semibold text-ink">Change email</h2>
      {me && <p className="text-sm text-ink-muted">Current email: {me.email}</p>}
      {requestEmailChange.isError && (
        <p role="alert" className={errorClass}>
          {requestEmailChange.error instanceof Error
            ? requestEmailChange.error.message
            : "Failed to request an email change. Please try again."}
        </p>
      )}
      {requestEmailChange.isSuccess && <p className={successClass}>{requestEmailChange.data.message}</p>}
      <form onSubmit={handleSubmit} className="space-y-2">
        <LabeledInput
          label="New email"
          ariaLabel="New email"
          type="email"
          value={newEmail}
          onChange={setNewEmail}
          autoComplete="email"
        />
        <LabeledInput
          label="Current password for email change"
          ariaLabel="Current password for email change"
          type="password"
          value={currentPassword}
          onChange={setCurrentPassword}
          autoComplete="current-password"
        />
        <LabeledInput
          label="Authenticator code for email change"
          ariaLabel="Authenticator code for email change"
          value={totpCode}
          onChange={setTotpCode}
          placeholder="6-digit code"
          autoComplete="one-time-code"
        />
        <button type="submit" disabled={requestEmailChange.isPending} className={primaryButtonClass}>
          Send confirmation email
        </button>
      </form>
    </section>
  );
}

function AmberApiKeyStatus({ hasKey }: { hasKey: boolean }) {
  return hasKey ? (
    <p className="rounded-md border border-flow/30 bg-flow-soft px-3 py-2 text-sm text-ink">Your Amber API key is configured.</p>
  ) : (
    <p className="rounded-md border border-solar/30 bg-solar-soft px-3 py-2 text-sm text-ink">No Amber API key set yet.</p>
  );
}

function AmberApiKeyExplanation() {
  return (
    <p className="text-sm text-ink-muted">
      To automatically pull your electricity prices from Amber, this app needs your personal
      Amber API key. You can generate one from your Amber account's developer/API settings page —
      look for a page about personal access tokens. Paste it below; it's stored securely and never
      shown again once saved.
    </p>
  );
}

function AmberApiKeySection() {
  const { data: me } = useMe();
  const [amberApiKey, setAmberApiKey] = useState("");
  const updateKey = useUpdateAmberApiKey();

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    updateKey.mutate(amberApiKey, {
      onSuccess: () => setAmberApiKey(""),
    });
  }

  return (
    <section className={cardClass}>
      <h2 className="font-display text-lg font-semibold text-ink">Amber Electric pricing</h2>
      <AmberApiKeyExplanation />
      {me && <AmberApiKeyStatus hasKey={me.has_amber_api_key} />}
      {updateKey.isError && (
        <p role="alert" className={errorClass}>
          {updateKey.error instanceof Error ? updateKey.error.message : "Failed to save the API key. Please try again."}
        </p>
      )}
      {updateKey.isSuccess && <p className={successClass}>Saved.</p>}
      <form onSubmit={handleSubmit} className="flex items-end gap-2">
        <label className="flex flex-col text-sm text-ink-muted">
          Amber API key
          <input
            aria-label="Amber API key"
            type="password"
            value={amberApiKey}
            onChange={(e) => setAmberApiKey(e.target.value)}
            placeholder={me?.has_amber_api_key ? "Enter a new key to replace the current one" : "Paste your Amber API key"}
            autoComplete="off"
            spellCheck={false}
            className="rounded-md border border-line bg-surface px-3 py-2 text-ink placeholder:text-ink-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-solar/60"
            required
          />
        </label>
        <button type="submit" disabled={updateKey.isPending} className={primaryButtonClass}>
          Save
        </button>
      </form>
    </section>
  );
}

function ManageTotpSection() {
  const [step, setStep] = useState<"start" | "verify" | "codes">("start");
  const [currentPassword, setCurrentPassword] = useState("");
  const [gateCode, setGateCode] = useState("");
  const [gateError, setGateError] = useState<string | null>(null);
  const [secret, setSecret] = useState<string | null>(null);
  const [qrDataUrl, setQrDataUrl] = useState<string | null>(null);
  const [newCode, setNewCode] = useState("");
  const [verifyError, setVerifyError] = useState<string | null>(null);
  const [recoveryCodes, setRecoveryCodes] = useState<string[] | null>(null);
  const [acknowledged, setAcknowledged] = useState(false);
  const startReenroll = useStartTotpReenroll();
  const confirmReenroll = useConfirmTotpReenroll();

  async function handleStartSubmit(event: FormEvent) {
    event.preventDefault();
    setGateError(null);
    try {
      const result = await startReenroll.mutateAsync({ current_password: currentPassword, totp_code: gateCode });
      setSecret(result.secret);
      setQrDataUrl(await QRCode.toDataURL(result.otpauth_uri));
      setStep("verify");
    } catch (err) {
      setGateError(errorMessage(err, "Failed to start authenticator reset. Please try again."));
    }
  }

  async function handleVerifySubmit(event: FormEvent) {
    event.preventDefault();
    setVerifyError(null);
    try {
      const result = await confirmReenroll.mutateAsync(newCode);
      setRecoveryCodes(result.recovery_codes);
      setStep("codes");
    } catch (err) {
      setVerifyError(errorMessage(err, "Incorrect code"));
    }
  }

  function handleDone() {
    setStep("start");
    setCurrentPassword("");
    setGateCode("");
    setSecret(null);
    setQrDataUrl(null);
    setNewCode("");
    setRecoveryCodes(null);
    setAcknowledged(false);
  }

  if (step === "codes" && recoveryCodes) {
    return (
      <section className={cardClass}>
        <h2 className="font-display text-lg font-semibold text-ink">Two-factor authentication</h2>
        <p className="text-sm text-ink-muted">Store these somewhere safe. Each works once if you lose your authenticator device.</p>
        <ul className="space-y-1 rounded-md border border-line bg-surface-2 p-4 font-mono text-sm text-ink">
          {recoveryCodes.map((rc) => (
            <li key={rc}>{rc}</li>
          ))}
        </ul>
        <label className="flex items-center gap-2 text-sm text-ink-muted">
          <input type="checkbox" checked={acknowledged} onChange={(e) => setAcknowledged(e.target.checked)} />
          I've saved these codes
        </label>
        <button disabled={!acknowledged} onClick={handleDone} className={primaryButtonClass}>
          Done
        </button>
      </section>
    );
  }

  if (step === "verify") {
    return (
      <section className={cardClass}>
        <h2 className="font-display text-lg font-semibold text-ink">Two-factor authentication</h2>
        {qrDataUrl && (
          <img src={qrDataUrl} alt="New TOTP QR code" width={200} height={200} className="rounded-md border border-line" />
        )}
        {secret && <p className="font-mono text-sm text-ink-muted">Manual entry key: {secret}</p>}
        <form onSubmit={handleVerifySubmit} className="space-y-2">
          {verifyError && (
            <p role="alert" className={errorClass}>
              {verifyError}
            </p>
          )}
          <LabeledInput
            label="New authenticator code"
            ariaLabel="New authenticator code"
            value={newCode}
            onChange={setNewCode}
            placeholder="6-digit code"
            autoComplete="one-time-code"
          />
          <button type="submit" disabled={confirmReenroll.isPending} className={primaryButtonClass}>
            Confirm
          </button>
        </form>
      </section>
    );
  }

  return (
    <section className={cardClass}>
      <h2 className="font-display text-lg font-semibold text-ink">Two-factor authentication</h2>
      <p className="text-sm text-ink-muted">Lost your device? Replace your authenticator and get new recovery codes.</p>
      <form onSubmit={handleStartSubmit} className="space-y-2">
        {gateError && (
          <p role="alert" className={errorClass}>
            {gateError}
          </p>
        )}
        <LabeledInput
          label="Current password for two-factor reset"
          ariaLabel="Current password for two-factor reset"
          type="password"
          value={currentPassword}
          onChange={setCurrentPassword}
          autoComplete="current-password"
        />
        <LabeledInput
          label="Current authenticator code"
          ariaLabel="Current authenticator code"
          value={gateCode}
          onChange={setGateCode}
          placeholder="6-digit code"
          autoComplete="one-time-code"
        />
        <button type="submit" disabled={startReenroll.isPending} className={primaryButtonClass}>
          Replace authenticator
        </button>
      </form>
    </section>
  );
}

export function AccountPage() {
  return (
    <div className="mx-auto max-w-2xl space-y-6 px-4 py-8">
      <h1 className="font-display text-2xl font-semibold text-ink">Account</h1>
      <ProfileSection />
      <ChangePasswordSection />
      <ChangeEmailSection />
      <ManageTotpSection />
      <AmberApiKeySection />
    </div>
  );
}
