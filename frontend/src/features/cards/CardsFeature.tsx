/**
 * Screens 02b to 02d: cards.
 *
 * A card here is a label — nickname, institution, last4, statement day and a
 * colour. `backend/app/schemas/cards.py` is the complete accepted field set,
 * and there is deliberately no field anywhere for a full number, an expiry, a
 * CVV or a PIN, so this screen never asks for one either.
 */

import { Route, Routes } from "react-router-dom";
import { CardList } from "./CardList";
import { CardDetail } from "./CardDetail";

export function CardsFeature() {
  return (
    <Routes>
      <Route path="" element={<CardList />} />
      <Route path=":cardId" element={<CardDetail />} />
    </Routes>
  );
}
