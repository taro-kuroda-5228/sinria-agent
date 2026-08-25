import assert from "node:assert/strict";
import test from "node:test";
import { sanitizeContext } from "../src/context.js";

test("structured result cards survive bounded context sanitization", () => {
  const result = sanitizeContext({
    snapshot: {
      title: "Search",
      url: "https://example.test/search?token=secret",
      text: "filters before results",
      cards: [
        { text: "Candidate One\nChief Executive Officer\nExample Health\nBoston", labels: ["Candidate One is reachable"] },
        { text: "Candidate Two token=abcdefghijklmnop", labels: [] },
      ],
      elements: [],
    },
  });
  assert.equal(result.snapshot.cards.length, 2);
  assert.match(result.snapshot.cards[0].text, /Chief Executive Officer/);
  assert.match(result.snapshot.cards[0].labels[0], /Candidate One/);
  assert.equal(JSON.stringify(result).includes("abcdefghijklmnop"), false);
});
