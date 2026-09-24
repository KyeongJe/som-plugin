export default [
  {
    files: ["**/*.mjs"],
    languageOptions: {
      ecmaVersion: 2023,
      sourceType: "module",
      globals: {
        process: "readonly", console: "readonly", Buffer: "readonly",
        URL: "readonly", TextEncoder: "readonly", TextDecoder: "readonly",
        setTimeout: "readonly", clearTimeout: "readonly",
        setInterval: "readonly", clearInterval: "readonly",
        structuredClone: "readonly", fetch: "readonly", AbortController: "readonly",
        __dirname: "readonly", __filename: "readonly", require: "readonly",
      },
    },
    rules: { "no-undef": "error", "no-unused-vars": "off" },
  },
];
