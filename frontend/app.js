/**
 * ============================================================
 * TERAVUE - SMART TRAVEL ITINERARY DECISION SUPPORT SYSTEM
 * Main Application Entry Point & Module Orchestrator
 * ============================================================
 * Architecture:
 * - js/data/reviews.js         : Accented reviews dictionary
 * - js/modules/map.js          : Leaflet map, layers, markers, polylines
 * - js/modules/state.js        : Global state (tripState), formatters, helpers
 * - js/modules/modals.js       : Modal dialogs (Place detail, favorites, profile)
 * - js/modules/screen1.js      : Screen 1 handlers (GPS, tags, inputs)
 * - js/modules/screen2.js      : Screen 2 handlers (Routes, Pareto comparison)
 * - js/modules/screen3.js      : Screen 3 handlers (Timeline, CRUD operations)
 * - js/modules/canvas_export.js: Canvas 2D image export
 * - js/modules/chatbot.js      : AI Chatbot floating widget
 * ============================================================
 */

$(document).ready(function () {
    console.log("Teravue DSS loaded successfully with modular architecture!");

    // Automatically restore active session if present
    if (typeof restoreSessionIfAny === 'function') {
        restoreSessionIfAny();
    }
});
