/**
 * The new app's placeholder at /next/: proves the static export, the shared tokens and the release pipeline before
 * any real page moves. Nothing links here; the first moved page (Inbox) replaces it as the pilot.
 */
export default function Home() {
  return (
    <main style={{ maxWidth: "var(--page-narrow)", margin: "0 auto", padding: "var(--gutter)" }}>
      <h1>Beacon</h1>
      <p>The new app is installed. Pages move here one at a time.</p>
    </main>
  );
}
