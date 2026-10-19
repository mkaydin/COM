/**
 * Tailwind config per page — GENERATED, do not edit by hand.
 *
 * The pages do not share a theme. `agent_profiles.html` and
 * `history.html` set `pixel-body: 16px` where the others use 18px, and
 * `council_chamber.html` omits letter-spacing. Those look deliberate — the
 * denser pages use smaller type — so each keeps its own and `build-css.js`
 * emits a stylesheet per page from the matching entry here.
 *
 * Regenerate with: node desktop/extract-configs.js
 *
 * This file is read by the build only. No browser loads it.
 */
module.exports = {
  // agent_profiles.html
  agent_profiles: {
    darkMode: "class",
    theme: {
      extend: {
        colors: {
          "obsidian": "#1a1a2e","background": "#141315","surface": "#141315","surface-dim": "#141315",
          "surface-container": "#201f21","surface-container-low": "#1c1b1d","surface-container-lowest": "#0e0e10",
          "surface-container-high": "#2a2a2b","surface-container-highest": "#353436","surface-variant": "#353436",
          "surface-bright": "#3a393b","surface-tint": "#c6c4df","parchment": "#e8d5b7","on-surface": "#e5e1e3",
          "on-surface-variant": "#c8c5cd","on-background": "#e5e1e3","on-primary": "#2f2e43",
          "on-primary-container": "#83829b","on-primary-fixed": "#1a1a2e","on-primary-fixed-variant": "#45455b",
          "on-secondary": "#3a2f1a","on-secondary-container": "#c8b699","on-secondary-fixed": "#241a07",
          "on-secondary-fixed-variant": "#51452f","on-tertiary": "#393012","on-tertiary-container": "#493f20",
          "on-tertiary-fixed": "#231b02","on-tertiary-fixed-variant": "#514627","on-error": "#690005",
          "on-error-container": "#ffdad6","primary": "#c6c4df","primary-container": "#1a1a2e",
          "primary-fixed": "#e2e0fc","primary-fixed-dim": "#c6c4df","secondary": "#d6c4a7",
          "secondary-container": "#544731","secondary-fixed": "#f3e0c2","secondary-fixed-dim": "#d6c4a7",
          "tertiary": "#d5c59d","tertiary-container": "#b9aa83","tertiary-fixed": "#f2e1b7",
          "tertiary-fixed-dim": "#d5c59d","runed-gold": "#ffd700","spectral-blue": "#87ceeb",
          "ember-glow": "#ff6b6b","mystic-purple": "#9b59b6","warning": "#f39c12","success": "#27ae60",
          "error": "#c0392b","error-container": "#93000a","neutral-gray": "#7f8c8d","outline": "#929097",
          "outline-variant": "#47464c","inverse-surface": "#e5e1e3","inverse-on-surface": "#313032",
          "inverse-primary": "#5d5c74"
        },
        borderRadius: { DEFAULT: "0.125rem", lg: "0.25rem", xl: "0.5rem", full: "0.75rem" },
        spacing: { xs: "4px", sm: "8px", md: "16px", lg: "24px", xl: "32px", xxl: "48px" },
        fontFamily: {
          "pixel-display": ["VT323", "monospace"],"pixel-heading": ["VT323", "monospace"],
          "pixel-body": ["VT323", "monospace"],"pixel-small": ["VT323", "monospace"]
        },
        fontSize: {
          "pixel-display": ["32px", { lineHeight: "1.2", letterSpacing: "1px", fontWeight: "700" }],
          "pixel-heading": ["24px", { lineHeight: "1.3", letterSpacing: "0.5px", fontWeight: "700" }],
          "pixel-body": ["16px", { lineHeight: "1.6", letterSpacing: "0.5px", fontWeight: "400" }],
          "pixel-small": ["12px", { lineHeight: "1.5", letterSpacing: "0px", fontWeight: "400" }]
        }
      }
    }
  },
  // council_chamber.html
  council_chamber: {
    darkMode: "class",
    theme: {
      extend: {
        colors: {
          "obsidian": "#1a1a2e","background": "#141315","surface": "#141315","surface-dim": "#141315",
          "surface-container": "#201f21","surface-container-low": "#1c1b1d","surface-container-lowest": "#0e0e10",
          "surface-container-high": "#2a2a2b","surface-container-highest": "#353436","surface-variant": "#353436",
          "surface-bright": "#3a393b","surface-tint": "#c6c4df","parchment": "#e8d5b7","on-surface": "#e5e1e3",
          "on-surface-variant": "#c8c5cd","on-background": "#e5e1e3","runed-gold": "#ffd700","spectral-blue": "#87ceeb",
          "ember-glow": "#ff6b6b","mystic-purple": "#9b59b6","warning": "#f39c12","success": "#27ae60",
          "error": "#c0392b","neutral-gray": "#7f8c8d","outline": "#929097",
          "outline-variant": "#47464c"
        },
        fontFamily: { "pixel-display": ["VT323", "monospace"], "pixel-heading": ["VT323", "monospace"], "pixel-body": ["VT323", "monospace"], "pixel-small": ["VT323", "monospace"] },
        fontSize: {
          "pixel-display": ["32px", { lineHeight: "1.2", fontWeight: "700" }],
          "pixel-heading": ["24px", { lineHeight: "1.3", fontWeight: "700" }],
          "pixel-body": ["18px", { lineHeight: "1.6", fontWeight: "400" }],
          "pixel-small": ["14px", { lineHeight: "1.5", fontWeight: "400" }]
        }
      }
    }
  },
  // council_landing_page.html
  council_landing_page: {
    darkMode: "class",
    theme: {
      extend: {
        colors: {
          "obsidian": "#1a1a2e",
          "background": "#141315",
          "surface": "#141315",
          "surface-dim": "#141315",
          "surface-container": "#201f21",
          "surface-container-low": "#1c1b1d",
          "surface-container-lowest": "#0e0e10",
          "surface-container-high": "#2a2a2b",
          "surface-container-highest": "#353436",
          "surface-variant": "#353436",
          "surface-bright": "#3a393b",
          "surface-tint": "#c6c4df",
          "parchment": "#e8d5b7",
          "on-surface": "#e5e1e3",
          "on-surface-variant": "#c8c5cd",
          "on-background": "#e5e1e3",
          "on-primary": "#2f2e43",
          "on-primary-container": "#83829b",
          "on-primary-fixed": "#1a1a2e",
          "on-primary-fixed-variant": "#45455b",
          "on-secondary": "#3a2f1a",
          "on-secondary-container": "#c8b699",
          "on-secondary-fixed": "#241a07",
          "on-secondary-fixed-variant": "#51452f",
          "on-tertiary": "#393012",
          "on-tertiary-container": "#493f20",
          "on-tertiary-fixed": "#231b02",
          "on-tertiary-fixed-variant": "#514627",
          "on-error": "#690005",
          "on-error-container": "#ffdad6",
          "primary": "#c6c4df",
          "primary-container": "#1a1a2e",
          "primary-fixed": "#e2e0fc",
          "primary-fixed-dim": "#c6c4df",
          "secondary": "#d6c4a7",
          "secondary-container": "#544731",
          "secondary-fixed": "#f3e0c2",
          "secondary-fixed-dim": "#d6c4a7",
          "tertiary": "#d5c59d",
          "tertiary-container": "#b9aa83",
          "tertiary-fixed": "#f2e1b7",
          "tertiary-fixed-dim": "#d5c59d",
          "runed-gold": "#ffd700",
          "spectral-blue": "#87ceeb",
          "ember-glow": "#ff6b6b",
          "mystic-purple": "#9b59b6",
          "warning": "#f39c12",
          "success": "#27ae60",
          "error": "#c0392b",
          "error-container": "#93000a",
          "neutral-gray": "#7f8c8d",
          "outline": "#929097",
          "outline-variant": "#47464c",
          "inverse-surface": "#e5e1e3",
          "inverse-on-surface": "#313032",
          "inverse-primary": "#5d5c74"
        },
        borderRadius: { DEFAULT: "0.125rem", lg: "0.25rem", xl: "0.5rem", full: "0.75rem" },
        spacing: { xs: "4px", sm: "8px", md: "16px", lg: "24px", xl: "32px", xxl: "48px" },
        fontFamily: {
          "pixel-display": ["VT323", "monospace"],
          "pixel-heading": ["VT323", "monospace"],
          "pixel-body": ["VT323", "monospace"],
          "pixel-small": ["VT323", "monospace"]
        },
        fontSize: {
          "pixel-display": ["32px", { lineHeight: "1.2", letterSpacing: "1px", fontWeight: "700" }],
          "pixel-heading": ["24px", { lineHeight: "1.3", letterSpacing: "0.5px", fontWeight: "700" }],
          "pixel-body": ["16px", { lineHeight: "1.6", letterSpacing: "0.5px", fontWeight: "400" }],
          "pixel-small": ["12px", { lineHeight: "1.5", letterSpacing: "0px", fontWeight: "400" }]
        }
      }
    }
  },
  // council_rules.html
  council_rules: {
    darkMode: "class",
    theme: {
      extend: {
        colors: {
          "obsidian": "#1a1a2e","background": "#141315","surface": "#141315","surface-dim": "#141315",
          "surface-container": "#201f21","surface-container-low": "#1c1b1d","surface-container-lowest": "#0e0e10",
          "surface-container-high": "#2a2a2b","surface-container-highest": "#353436","surface-variant": "#353436",
          "surface-bright": "#3a393b","surface-tint": "#c6c4df","parchment": "#e8d5b7","on-surface": "#e5e1e3",
          "on-surface-variant": "#c8c5cd","on-background": "#e5e1e3","on-primary": "#2f2e43",
          "on-primary-container": "#83829b","on-primary-fixed": "#1a1a2e","on-primary-fixed-variant": "#45455b",
          "on-secondary": "#3a2f1a","on-secondary-container": "#c8b699","on-secondary-fixed": "#241a07",
          "on-secondary-fixed-variant": "#51452f","on-tertiary": "#393012","on-tertiary-container": "#493f20",
          "on-tertiary-fixed": "#231b02","on-tertiary-fixed-variant": "#514627","on-error": "#690005",
          "on-error-container": "#ffdad6","primary": "#c6c4df","primary-container": "#1a1a2e",
          "primary-fixed": "#e2e0fc","primary-fixed-dim": "#c6c4df","secondary": "#d6c4a7",
          "secondary-container": "#544731","secondary-fixed": "#f3e0c2","secondary-fixed-dim": "#d6c4a7",
          "tertiary": "#d5c59d","tertiary-container": "#b9aa83","tertiary-fixed": "#f2e1b7",
          "tertiary-fixed-dim": "#d5c59d","runed-gold": "#ffd700","spectral-blue": "#87ceeb",
          "ember-glow": "#ff6b6b","mystic-purple": "#9b59b6","warning": "#f39c12","success": "#27ae60",
          "error": "#c0392b","error-container": "#93000a","neutral-gray": "#7f8c8d","outline": "#929097",
          "outline-variant": "#47464c","inverse-surface": "#e5e1e3","inverse-on-surface": "#313032",
          "inverse-primary": "#5d5c74"
        },
        borderRadius: { DEFAULT: "0.125rem", lg: "0.25rem", xl: "0.5rem", full: "0.75rem" },
        spacing: { xs: "4px", sm: "8px", md: "16px", lg: "24px", xl: "32px", xxl: "48px" },
        fontFamily: {
          "pixel-display": ["VT323", "monospace"],"pixel-heading": ["VT323", "monospace"],
          "pixel-body": ["VT323", "monospace"],"pixel-small": ["VT323", "monospace"]
        },
        fontSize: {
          "pixel-display": ["32px", { lineHeight: "1.2", letterSpacing: "1px", fontWeight: "700" }],
          "pixel-heading": ["24px", { lineHeight: "1.3", letterSpacing: "0.5px", fontWeight: "700" }],
          "pixel-body": ["16px", { lineHeight: "1.6", letterSpacing: "0.5px", fontWeight: "400" }],
          "pixel-small": ["12px", { lineHeight: "1.5", letterSpacing: "0px", fontWeight: "400" }]
        }
      }
    }
  },
  // deliberation_view.html
  deliberation_view: {
    darkMode: "class",
    theme: {
      extend: {
        colors: {
          "obsidian": "#1a1a2e","background": "#141315","surface": "#141315","surface-dim": "#141315",
          "surface-container": "#201f21","surface-container-low": "#1c1b1d","surface-container-lowest": "#0e0e10",
          "surface-container-high": "#2a2a2b","surface-container-highest": "#353436","surface-variant": "#353436",
          "surface-bright": "#3a393b","surface-tint": "#c6c4df","parchment": "#e8d5b7","on-surface": "#e5e1e3",
          "on-surface-variant": "#c8c5cd","on-background": "#e5e1e3","on-primary": "#2f2e43",
          "on-primary-container": "#83829b","on-primary-fixed": "#1a1a2e","on-primary-fixed-variant": "#45455b",
          "on-secondary": "#3a2f1a","on-secondary-container": "#c8b699","on-secondary-fixed": "#241a07",
          "on-secondary-fixed-variant": "#51452f","on-tertiary": "#393012","on-tertiary-container": "#493f20",
          "on-tertiary-fixed": "#231b02","on-tertiary-fixed-variant": "#514627","on-error": "#690005",
          "on-error-container": "#ffdad6","primary": "#c6c4df","primary-container": "#1a1a2e",
          "primary-fixed": "#e2e0fc","primary-fixed-dim": "#c6c4df","secondary": "#d6c4a7",
          "secondary-container": "#544731","secondary-fixed": "#f3e0c2","secondary-fixed-dim": "#d6c4a7",
          "tertiary": "#d5c59d","tertiary-container": "#b9aa83","tertiary-fixed": "#f2e1b7",
          "tertiary-fixed-dim": "#d5c59d","runed-gold": "#ffd700","spectral-blue": "#87ceeb",
          "ember-glow": "#ff6b6b","mystic-purple": "#9b59b6","warning": "#f39c12","success": "#27ae60",
          "error": "#c0392b","error-container": "#93000a","neutral-gray": "#7f8c8d","outline": "#929097",
          "outline-variant": "#47464c","inverse-surface": "#e5e1e3","inverse-on-surface": "#313032",
          "inverse-primary": "#5d5c74"
        },
        borderRadius: { DEFAULT: "0.125rem", lg: "0.25rem", xl: "0.5rem", full: "0.75rem" },
        spacing: { xs: "4px", sm: "8px", md: "16px", lg: "24px", xl: "32px", xxl: "48px" },
        fontFamily: {
          "pixel-display": ["VT323", "monospace"],"pixel-heading": ["VT323", "monospace"],
          "pixel-body": ["VT323", "monospace"],"pixel-small": ["VT323", "monospace"]
        },
        fontSize: {
          "pixel-display": ["32px", { lineHeight: "1.2", letterSpacing: "1px", fontWeight: "700" }],
          "pixel-heading": ["24px", { lineHeight: "1.3", letterSpacing: "0.5px", fontWeight: "700" }],
          "pixel-body": ["16px", { lineHeight: "1.6", letterSpacing: "0.5px", fontWeight: "400" }],
          "pixel-small": ["12px", { lineHeight: "1.5", letterSpacing: "0px", fontWeight: "400" }]
        }
      }
    }
  },
  // divination.html
  divination: {
    darkMode: "class",
    theme: { extend: {
      colors: {
        "obsidian": "#1a1a2e","surface": "#141315","surface-dim": "#141315",
        "surface-container": "#201f21","surface-container-low": "#1c1b1d","surface-container-lowest": "#0e0e10",
        "surface-container-high": "#2a2a2b","surface-container-highest": "#353436","surface-variant": "#353436",
        "parchment": "#e8d5b7","on-surface": "#e5e1e3","on-surface-variant": "#c8c5cd",
        "runed-gold": "#ffd700","spectral-blue": "#87ceeb","ember-glow": "#ff6b6b",
        "mystic-purple": "#9b59b6","warning": "#f39c12","success": "#27ae60",
        "error": "#c0392b","neutral-gray": "#7f8c8d"
      },
      fontFamily: { "pixel-display": ["VT323","monospace"], "pixel-heading": ["VT323","monospace"], "pixel-body": ["VT323","monospace"], "pixel-small": ["VT323","monospace"] },
      fontSize: {
        "pixel-display": ["32px",{lineHeight:"1.2",fontWeight:"700"}],
        "pixel-heading": ["24px",{lineHeight:"1.3",fontWeight:"700"}],
        "pixel-body": ["18px",{lineHeight:"1.6",fontWeight:"400"}],
        "pixel-small": ["14px",{lineHeight:"1.5",fontWeight:"400"}]
      }
    }}
  },
  // forum.html
  forum: {
    darkMode: "class",
    theme: {
      extend: {
        colors: {
          "obsidian": "#1a1a2e","background": "#141315","surface": "#141315","surface-dim": "#141315",
          "surface-container": "#201f21","surface-container-low": "#1c1b1d","surface-container-lowest": "#0e0e10",
          "surface-container-high": "#2a2a2b","surface-container-highest": "#353436","surface-variant": "#353436",
          "surface-bright": "#3a393b","surface-tint": "#c6c4df","parchment": "#e8d5b7","on-surface": "#e5e1e3",
          "on-surface-variant": "#c8c5cd","on-background": "#e5e1e3","runed-gold": "#ffd700","spectral-blue": "#87ceeb",
          "ember-glow": "#ff6b6b","mystic-purple": "#9b59b6","warning": "#f39c12","success": "#27ae60",
          "error": "#c0392b","neutral-gray": "#7f8c8d","outline": "#929097","outline-variant": "#47464c"
        },
        fontFamily: {
          "pixel-display": ["VT323", "monospace"],
          "pixel-heading": ["VT323", "monospace"],
          "pixel-body": ["VT323", "monospace"],
          "pixel-small": ["VT323", "monospace"],
          "ui": ["Be Vietnam Pro", "system-ui", "sans-serif"]
        },
        fontSize: {
          "pixel-display": ["32px", { lineHeight: "1.2", letterSpacing: "1px", fontWeight: "700" }],
          "pixel-heading": ["24px", { lineHeight: "1.3", letterSpacing: "0.5px", fontWeight: "700" }],
          "pixel-body": ["18px", { lineHeight: "1.6", letterSpacing: "0.5px", fontWeight: "400" }],
          "pixel-small": ["14px", { lineHeight: "1.5", letterSpacing: "0px", fontWeight: "400" }]
        }
      }
    }
  },
  // history.html
  history: {
    darkMode: "class",
    theme: {
      extend: {
        colors: {
          "obsidian": "#1a1a2e","background": "#141315","surface": "#141315","surface-dim": "#141315",
          "surface-container": "#201f21","surface-container-low": "#1c1b1d","surface-container-lowest": "#0e0e10",
          "surface-container-high": "#2a2a2b","surface-container-highest": "#353436","surface-variant": "#353436",
          "surface-bright": "#3a393b","surface-tint": "#c6c4df","parchment": "#e8d5b7","on-surface": "#e5e1e3",
          "on-surface-variant": "#c8c5cd","on-background": "#e5e1e3","on-primary": "#2f2e43",
          "on-primary-container": "#83829b","on-primary-fixed": "#1a1a2e","on-primary-fixed-variant": "#45455b",
          "on-secondary": "#3a2f1a","on-secondary-container": "#c8b699","on-secondary-fixed": "#241a07",
          "on-secondary-fixed-variant": "#51452f","on-tertiary": "#393012","on-tertiary-container": "#493f20",
          "on-tertiary-fixed": "#231b02","on-tertiary-fixed-variant": "#514627","on-error": "#690005",
          "on-error-container": "#ffdad6","primary": "#c6c4df","primary-container": "#1a1a2e",
          "primary-fixed": "#e2e0fc","primary-fixed-dim": "#c6c4df","secondary": "#d6c4a7",
          "secondary-container": "#544731","secondary-fixed": "#f3e0c2","secondary-fixed-dim": "#d6c4a7",
          "tertiary": "#d5c59d","tertiary-container": "#b9aa83","tertiary-fixed": "#f2e1b7",
          "tertiary-fixed-dim": "#d5c59d","runed-gold": "#ffd700","spectral-blue": "#87ceeb",
          "ember-glow": "#ff6b6b","mystic-purple": "#9b59b6","warning": "#f39c12","success": "#27ae60",
          "error": "#c0392b","error-container": "#93000a","neutral-gray": "#7f8c8d","outline": "#929097",
          "outline-variant": "#47464c","inverse-surface": "#e5e1e3","inverse-on-surface": "#313032",
          "inverse-primary": "#5d5c74"
        },
        borderRadius: { DEFAULT: "0.125rem", lg: "0.25rem", xl: "0.5rem", full: "0.75rem" },
        spacing: { xs: "4px", sm: "8px", md: "16px", lg: "24px", xl: "32px", xxl: "48px" },
        fontFamily: {
          "pixel-display": ["VT323", "monospace"],"pixel-heading": ["VT323", "monospace"],
          "pixel-body": ["VT323", "monospace"],"pixel-small": ["VT323", "monospace"]
        },
        fontSize: {
          "pixel-display": ["32px", { lineHeight: "1.2", letterSpacing: "1px", fontWeight: "700" }],
          "pixel-heading": ["24px", { lineHeight: "1.3", letterSpacing: "0.5px", fontWeight: "700" }],
          "pixel-body": ["16px", { lineHeight: "1.6", letterSpacing: "0.5px", fontWeight: "400" }],
          "pixel-small": ["12px", { lineHeight: "1.5", letterSpacing: "0px", fontWeight: "400" }]
        }
      }
    }
  },
  // llm_settings.html
  llm_settings: {
    darkMode: "class",
    theme: { extend: {
      colors: {
        "obsidian":"#1a1a2e","background":"#141315","surface":"#141315","surface-dim":"#141315",
        "surface-container":"#201f21","surface-container-low":"#1c1b1d","surface-container-lowest":"#0e0e10",
        "surface-container-high":"#2a2a2b","surface-container-highest":"#353436","surface-variant":"#353436",
        "parchment":"#e8d5b7","on-surface":"#e5e1e3","on-surface-variant":"#c8c5cd","runed-gold":"#ffd700",
        "spectral-blue":"#87ceeb","ember-glow":"#ff6b6b","mystic-purple":"#9b59b6","warning":"#f39c12",
        "success":"#27ae60","error":"#c0392b","neutral-gray":"#7f8c8d","outline":"#929097","outline-variant":"#47464c"
      },
      fontFamily: { "pixel-display":["VT323","monospace"],"pixel-heading":["VT323","monospace"],
                    "pixel-body":["VT323","monospace"],"pixel-small":["VT323","monospace"] },
      fontSize: {
        "pixel-display":["32px",{lineHeight:"1.2",letterSpacing:"1px",fontWeight:"700"}],
        "pixel-heading":["24px",{lineHeight:"1.3",letterSpacing:"0.5px",fontWeight:"700"}],
        "pixel-body":["18px",{lineHeight:"1.6",letterSpacing:"0.5px",fontWeight:"400"}],
        "pixel-small":["14px",{lineHeight:"1.5",letterSpacing:"0px",fontWeight:"400"}]
      }
    }}
  },
  // models.html
  models: {
    darkMode: "class",
    theme: { extend: {
      colors: {
        "obsidian":"#1a1a2e","background":"#141315","surface":"#141315","surface-dim":"#141315",
        "surface-container":"#201f21","surface-container-low":"#1c1b1d","surface-container-lowest":"#0e0e10",
        "surface-container-high":"#2a2a2b","surface-container-highest":"#353436","surface-variant":"#353436",
        "parchment":"#e8d5b7","on-surface":"#e5e1e3","on-surface-variant":"#c8c5cd","runed-gold":"#ffd700",
        "spectral-blue":"#87ceeb","ember-glow":"#ff6b6b","mystic-purple":"#9b59b6","warning":"#f39c12",
        "success":"#27ae60","error":"#c0392b","neutral-gray":"#7f8c8d","outline":"#929097","outline-variant":"#47464c"
      },
      fontFamily: { "pixel-display":["VT323","monospace"],"pixel-heading":["VT323","monospace"],
                    "pixel-body":["VT323","monospace"],"pixel-small":["VT323","monospace"] },
      fontSize: {
        "pixel-display":["32px",{lineHeight:"1.2",letterSpacing:"1px",fontWeight:"700"}],
        "pixel-heading":["24px",{lineHeight:"1.3",letterSpacing:"0.5px",fontWeight:"700"}],
        "pixel-body":["18px",{lineHeight:"1.6",letterSpacing:"0.5px",fontWeight:"400"}],
        "pixel-small":["14px",{lineHeight:"1.5",letterSpacing:"0px",fontWeight:"400"}]
      }
    }}
  },
  // runic_library.html
  runic_library: {
    darkMode: "class",
    theme: { extend: {
      colors: {
        "obsidian": "#1a1a2e","surface": "#141315","surface-dim": "#141315",
        "surface-container": "#201f21","surface-container-low": "#1c1b1d","surface-container-lowest": "#0e0e10",
        "surface-container-high": "#2a2a2b","surface-container-highest": "#353436",
        "parchment": "#e8d5b7","on-surface": "#e5e1e3","runed-gold": "#ffd700",
        "spectral-blue": "#87ceeb","ember-glow": "#ff6b6b","mystic-purple": "#9b59b6",
        "warning": "#f39c12","success": "#27ae60","error": "#c0392b","neutral-gray": "#7f8c8d","outline": "#929097",
      },
      fontFamily: { "pixel-display": ["VT323","monospace"], "pixel-heading": ["VT323","monospace"], "pixel-body": ["VT323","monospace"], "pixel-small": ["VT323","monospace"] },
      fontSize: {
        "pixel-display": ["32px",{lineHeight:"1.2",fontWeight:"700"}],
        "pixel-heading": ["24px",{lineHeight:"1.3",fontWeight:"700"}],
        "pixel-body": ["18px",{lineHeight:"1.6",fontWeight:"400"}],
        "pixel-small": ["14px",{lineHeight:"1.5",fontWeight:"400"}]
      }
    }}
  },
  // setup.html
  setup: {
    darkMode: "class",
    theme: { extend: {
      colors: {
        "obsidian":"#1a1a2e","background":"#141315","surface-container":"#201f21",
        "surface-container-lowest":"#0e0e10","surface-container-highest":"#353436",
        "parchment":"#e8d5b7","runed-gold":"#ffd700","spectral-blue":"#87ceeb",
        "ember-glow":"#ff6b6b","mystic-purple":"#9b59b6","success":"#27ae60",
        "error":"#c0392b","neutral-gray":"#7f8c8d","warning":"#f39c12",
        "outline-variant":"#47464c"
      },
      fontFamily: { "pixel-display":["VT323","monospace"],"pixel-heading":["VT323","monospace"],
                    "pixel-body":["VT323","monospace"],"pixel-small":["VT323","monospace"] },
      fontSize: {
        "pixel-display":["32px",{lineHeight:"1.2",letterSpacing:"1px",fontWeight:"700"}],
        "pixel-heading":["24px",{lineHeight:"1.3",letterSpacing:"0.5px",fontWeight:"700"}],
        "pixel-body":["18px",{lineHeight:"1.6",letterSpacing:"0.5px",fontWeight:"400"}],
        "pixel-small":["14px",{lineHeight:"1.5",letterSpacing:"0px",fontWeight:"400"}]
      }
    }}
  },
  // topic.html
  topic: {
    darkMode: "class",
    theme: {
      extend: {
        colors: {
          "obsidian": "#1a1a2e","background": "#141315","surface": "#141315","surface-dim": "#141315",
          "surface-container": "#201f21","surface-container-low": "#1c1b1d","surface-container-lowest": "#0e0e10",
          "surface-container-high": "#2a2a2b","surface-container-highest": "#353436","surface-variant": "#353436",
          "surface-bright": "#3a393b","surface-tint": "#c6c4df","parchment": "#e8d5b7","on-surface": "#e5e1e3",
          "on-surface-variant": "#c8c5cd","on-background": "#e5e1e3","runed-gold": "#ffd700","spectral-blue": "#87ceeb",
          "ember-glow": "#ff6b6b","mystic-purple": "#9b59b6","warning": "#f39c12","success": "#27ae60",
          "error": "#c0392b","neutral-gray": "#7f8c8d","outline": "#929097","outline-variant": "#47464c"
        },
        fontFamily: {
          "pixel-display": ["VT323","monospace"], "pixel-heading": ["VT323","monospace"],
          "pixel-body": ["VT323","monospace"], "pixel-small": ["VT323","monospace"]
        },
        fontSize: {
          "pixel-display": ["32px",{lineHeight:"1.2",letterSpacing:"1px",fontWeight:"700"}],
          "pixel-heading": ["24px",{lineHeight:"1.3",letterSpacing:"0.5px",fontWeight:"700"}],
          "pixel-body": ["18px",{lineHeight:"1.6",letterSpacing:"0.5px",fontWeight:"400"}],
          "pixel-small": ["14px",{lineHeight:"1.5",letterSpacing:"0px",fontWeight:"400"}]
        }
      }
    }
  },
};
