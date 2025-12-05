// ESLint flat config for Movie Searcher frontend
// Philosophy: Comments should explain WHY, not WHAT. 
// Function names should be self-documenting. JSDoc-style /** */ comments
// that merely restate the function name are noise.

import noBlockComments from './eslint-rules/no-block-comments.js';

// Define local plugin with our custom rules
const localPlugin = {
  rules: {
    'no-block-comments': noBlockComments
  }
};

export default [
  {
    files: ["**/*.js"],
    ignores: ["eslint-rules/**", "scripts/**"],
    plugins: {
      local: localPlugin
    },
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: "script",
      globals: {
        // Browser globals
        window: "readonly",
        document: "readonly",
        console: "readonly",
        fetch: "readonly",
        alert: "readonly",
        confirm: "readonly",
        setTimeout: "readonly",
        setInterval: "readonly",
        clearInterval: "readonly",
        clearTimeout: "readonly",
        localStorage: "readonly",
        location: "readonly",
        history: "readonly",
        HTMLElement: "readonly",
        Element: "readonly",
        Event: "readonly",
        KeyboardEvent: "readonly",
        MouseEvent: "readonly",
        MutationObserver: "readonly",
        IntersectionObserver: "readonly",
        ResizeObserver: "readonly",
        Image: "readonly",
        Audio: "readonly",
        FormData: "readonly",
        URLSearchParams: "readonly",
        URL: "readonly",
        Blob: "readonly",
        FileReader: "readonly",
        AbortController: "readonly",
        requestAnimationFrame: "readonly",
        cancelAnimationFrame: "readonly",
        getComputedStyle: "readonly",
        // Project globals (functions defined in other JS files)
        showStatus: "readonly",
        openFolder: "readonly",
        showAddToPlaylistMenu: "readonly",
        copyMovieToLocal: "readonly",
        hideMovie: "readonly",
        formatPlaybackTime: "readonly",
        formatDuration: "readonly",
        navigateTo: "readonly",
        loadPage: "readonly",
        getAvailableMenuActions: "readonly",
        getActionLabel: "readonly",
        getActionClassName: "readonly",
        renderMovieActionMenu: "readonly",
        setupMovieMenuListeners: "readonly",
        MOVIE_MENU_ACTIONS: "readonly",
      }
    },
    linterOptions: {
      reportUnusedDisableDirectives: true,
    },
    rules: {
      // BLOCK COMMENTS ARE FORBIDDEN
      // This project uses // comments exclusively. See docs/agents.md for rationale.
      "local/no-block-comments": "error",
      
      // Don't be overly strict on other things - this is a simple frontend
      "no-unused-vars": ["warn", { "argsIgnorePattern": "^_" }],
    }
  }
];
