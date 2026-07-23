//eventi di demo su database mongo

db = db.getSiblingDB("sistema_eventi");

db.eventi_osservati.insertMany([
  // ── 20 Luglio ──
  {
    timestamp: "2026-07-20T13:17:05",
    camera_id: "entrance_clients",
    location: "ingresso principale per i clienti",
    description: "una persona cammina lentamente per la stanza",
    event_type: "movement",
    metadata: { confidence: 0.93, tags: ["person", "walking"] }
  },
  {
    timestamp: "2026-07-20T13:43:44",
    camera_id: "reception_hall",
    location: "sportello",
    description: "piccolo gruppo di persone discute",
    event_type: "crowd",
    metadata: { confidence: 0.9, tags: ["group", "discussion"] }
  },
  {
    timestamp: "2026-07-20T14:40:44",
    camera_id: "corridor_1",
    location: "corridoio principale",
    description: "una persona si dirige verso la porta",
    event_type: "movement",
    metadata: { confidence: 0.89, tags: ["person", "door"] }
  },
  {
    timestamp: "2026-07-20T15:15:26",
    camera_id: "reception_hall",
    location: "sportello",
    description: "persona ferma al distributore automatico",
    event_type: "idle",
    metadata: { confidence: 0.86, tags: ["person", "vending_machine"] }
  },

  // ── 21 Luglio ──
  {
    timestamp: "2026-07-21T07:19:21",
    camera_id: "entrance_clients",
    location: "ingresso principale per i clienti",
    description: "una dipendente entra nella stanza",
    event_type: "movement",
    metadata: { confidence: 0.91, tags: ["employee", "transit"] }
  },
  {
    timestamp: "2026-07-21T09:17:26",
    camera_id: "vault",
    location: "camera blindata",
    description: "una dipendente entra nella stanza",
    event_type: "movement",
    metadata: { confidence: 0.96, tags: ["employee", "transit"] }
  },
  {
    timestamp: "2026-07-21T11:51:13",
    camera_id: "corridor_2",
    location: "corridoio secondario",
    description: "una persona cammina lentamente per la stanza",
    event_type: "movement",
    metadata: { confidence: 0.95, tags: ["person", "walking"] }
  },
  {
    timestamp: "2026-07-21T14:25:39",
    camera_id: "reception_hall",
    location: "sportello",
    description: "gruppo di dipendenti a lavoro",
    event_type: "crowd",
    metadata: { confidence: 0.87, tags: ["group", "employee"] }
  },
  {
    timestamp: "2026-07-21T22:38:36",
    camera_id: "corridor_2",
    location: "corridoio secondario",
    description: "persona ferma al cellulare",
    event_type: "idle",
    metadata: { confidence: 0.68, tags: ["person", "phone"] }
  },

  // ── 22 Luglio ──
  {
    timestamp: "2026-07-22T15:54:56",
    camera_id: "corridor_1",
    location: "corridoio principale",
    description: "persona ferma al cellulare",
    event_type: "idle",
    metadata: { confidence: 0.84, tags: ["person", "phone"] }
  },
  {
    timestamp: "2026-07-22T21:40:58",
    camera_id: "entrance_clients",
    location: "ingresso principale per i clienti",
    description: "gruppo di persone stazionano davanti alla porta da oltre 10 minuti",
    event_type: "crowd",
    metadata: { confidence: 0.76, tags: ["group", "door", "loitering"] }
  },
  {
    timestamp: "2026-07-22T23:00:53",
    camera_id: "reception_hall",
    location: "sportello",
    description: "una persona cammina lentamente per la stanza",
    event_type: "movement",
    metadata: { confidence: 0.62, tags: ["person", "walking"] }
  },

  // ── 23 Luglio ──
  {
    timestamp: "2026-07-23T01:46:02",
    camera_id: "exit",
    location: "uscita principale",
    description: "persona ferma al distributore automatico",
    event_type: "idle",
    metadata: { confidence: 0.59, tags: ["person", "vending_machine"] }
  },
  {
    timestamp: "2026-07-23T05:19:47",
    camera_id: "corridor_1",
    location: "corridoio principale",
    description: "una persona si dirige verso la porta",
    event_type: "movement",
    metadata: { confidence: 0.65, tags: ["person", "door"] }
  },
  {
    timestamp: "2026-07-23T14:06:29",
    camera_id: "exit",
    location: "uscita principale",
    description: "una persona si dirige verso la porta",
    event_type: "movement",
    metadata: { confidence: 0.91, tags: ["person", "door"] }
  },

  // ── 25 Luglio ──
  {
    timestamp: "2026-07-25T08:53:37",
    camera_id: "corridor_1",
    location: "corridoio principale",
    description: "una dipendente entra nella stanza",
    event_type: "movement",
    metadata: { confidence: 0.94, tags: ["employee", "transit"] }
  },

  // ── 26 Luglio ──
  {
    timestamp: "2026-07-26T09:11:43",
    camera_id: "vault",
    location: "camera blindata",
    description: "una dipendente entra nella stanza",
    event_type: "movement",
    metadata: { confidence: 0.97, tags: ["employee", "transit"] }
  },
  {
    timestamp: "2026-07-26T20:43:32",
    camera_id: "corridor_1",
    location: "corridoio principale",
    description: "dipendente in pausa",
    event_type: "idle",
    metadata: { confidence: 0.81, tags: ["employee", "break"] }
  },
  {
    timestamp: "2026-07-26T20:50:58",
    camera_id: "entrance_clients",
    location: "ingresso principale per i clienti",
    description: "una persona si dirige verso la porta",
    event_type: "movement",
    metadata: { confidence: 0.79, tags: ["person", "door"] }
  },

  // ── 27 Luglio ──
  {
    timestamp: "2026-07-27T01:28:28",
    camera_id: "vault",
    location: "camera blindata",
    description: "una persona cammina lentamente per la stanza",
    event_type: "movement",
    metadata: { confidence: 0.88, tags: ["person", "walking"] }
  },
  {
    timestamp: "2026-07-27T09:59:36",
    camera_id: "reception_hall",
    location: "sportello",
    description: "gruppo di dipendenti a lavoro",
    event_type: "crowd",
    metadata: { confidence: 0.88, tags: ["group", "employee"] }
  },
  {
    timestamp: "2026-07-27T13:21:02",
    camera_id: "entrance_clients",
    location: "ingresso principale per i clienti",
    description: "piccolo gruppo di persone discute",
    event_type: "crowd",
    metadata: { confidence: 0.92, tags: ["group", "discussion"] }
  },

  // ── 28 Luglio ──
  {
    timestamp: "2026-07-28T00:14:47",
    camera_id: "reception_hall",
    location: "sportello",
    description: "una persona cammina lentamente per la stanza",
    event_type: "movement",
    metadata: { confidence: 0.6, tags: ["person", "walking"] }
  },
  {
    timestamp: "2026-07-28T03:17:33",
    camera_id: "corridor_2",
    location: "corridoio secondario",
    description: "persona ferma al distributore automatico",
    event_type: "idle",
    metadata: { confidence: 0.57, tags: ["person", "vending_machine"] }
  },
  {
    timestamp: "2026-07-28T11:06:10",
    camera_id: "vault",
    location: "camera blindata",
    description: "una dipendente entra nella stanza",
    event_type: "movement",
    metadata: { confidence: 0.98, tags: ["employee", "transit"] }
  },
  {
    timestamp: "2026-07-28T18:16:32",
    camera_id: "exit",
    location: "uscita principale",
    description: "una persona cammina lentamente per la stanza",
    event_type: "movement",
    metadata: { confidence: 0.92, tags: ["person", "walking"] }
  },
  {
    timestamp: "2026-07-28T22:33:20",
    camera_id: "vault",
    location: "camera blindata",
    description: "una persona cammina lentamente per la stanza",
    event_type: "movement",
    metadata: { confidence: 0.85, tags: ["person", "walking"] }
  },

  // ── 29 Luglio ──
  {
    timestamp: "2026-07-29T04:30:46",
    camera_id: "corridor_1",
    location: "corridoio principale",
    description: "una persona si dirige verso la porta",
    event_type: "movement",
    metadata: { confidence: 0.63, tags: ["person", "door"] }
  },
  {
    timestamp: "2026-07-29T05:44:06",
    camera_id: "reception_hall",
    location: "sportello",
    description: "persona ferma al cellulare",
    event_type: "idle",
    metadata: { confidence: 0.58, tags: ["person", "phone"] }
  },
  {
    timestamp: "2026-07-29T05:59:32",
    camera_id: "corridor_2",
    location: "corridoio secondario",
    description: "una persona cammina lentamente per la stanza",
    event_type: "movement",
    metadata: { confidence: 0.61, tags: ["person", "walking"] }
  }
]);

print("Seed completato: " + db.eventi_osservati.countDocuments({}) + " eventi inseriti in 'sistema_eventi.eventi_osservati'.");