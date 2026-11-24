// What the page modules read from outside themselves: the deployment settings (/config.js, hub/settings.py),
// the language switch (the head script and i18n.js), the shell (hub.js) and the vendor graph libraries that
// knowledge.html loads as classic scripts. Everything else is imported.

interface BeaconConfig {
  owner: { id: string; name: string; names: string[] };
  hub: { machine: string; https: string; agent_home: string };
  machines: string[];
  machine_icons: Record<string, string>;
  project_names: Record<string, string>;
  site_names: Record<string, string>;
  timezone: string;
  game: string;
}

interface Window {
  BEACON: BeaconConfig;
  hubLang: 'en' | 'cs';
  hubLocale: string;
  /** Czech for a hub string when Czech is on (i18n.js); absent in English. */
  hubT?: (text: string) => string;
  HUB_CS?: { exact: Record<string, string>; patterns: Record<string, string> };
  /** Re-reads the Decisions badge after an answer (hub.js). */
  hubRefreshBadge?: () => void;
  hubFeedback?: boolean;
  modernScreenshot?: { domToPng: (node: Node, options?: object) => Promise<string> };
}

/** force-graph (vendor/force-graph.min.js) and d3-force (vendor/d3-*.min.js), classic scripts on Knowledge. */
declare const ForceGraph: any;
declare const d3: any;
