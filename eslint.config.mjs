// Lint for the TypeScript workspace (ts/apps/, ts/packages/): typescript-eslint's strictest type-checked set, zero findings.
// The old pages in web/ keep Biome's rules (biome.json) until they are deleted.
import tseslint from "typescript-eslint";

export default tseslint.config(
  { ignores: ["**/dist/**", "**/bundle/**", "**/.next/**", "**/out/**", "**/next-env.d.ts", "web/**", "**/*.mjs"] },
  {
    files: ["ts/apps/**/*.{ts,tsx}", "ts/packages/**/*.ts"],
    extends: [tseslint.configs.strictTypeChecked, tseslint.configs.stylisticTypeChecked],
    languageOptions: { parserOptions: { projectService: true, tsconfigRootDir: import.meta.dirname } },
    rules: {
      "@typescript-eslint/no-explicit-any": "error",
      "@typescript-eslint/consistent-type-imports": "error",
      "@typescript-eslint/switch-exhaustiveness-check": "error",
    },
  },
);
