given up: 90
by reason: [('body gate', 85), ('names', 5)]
by month scraped: [('2026-09', 60), ('2026-10', 30)]
by category: [('international', 25), ('economy', 14), ('politics', 14), ('other', 12), ('crime', 10), ('regional', 6), ('environment', 3), ('education', 3)]
by source: [('The Hindu', 33), ('Times of India', 20), ('NDTV', 13), ('TechCrunch', 7), ('Economic Times', 5), ('BBC', 5), ('Al Jazeera', 5), ('Wired', 2)]
failing checks: [(('gap_ok',), 54), (('script_ok',), 27), (('names',), 5), (('number_ok',), 4)]
extra numbers in Hindi: [('2', 2), ('7', 2), ('5', 2), ('3', 2), ('10', 2), ('8', 1), ('9', 1), ('20', 1), ('90', 1), ('6', 1)]
missing numbers: [('1', 3), ('2', 1)]
bad script samples: ['ETब्यूरो', 'एवरेस्टIMS', 'मडििला', 'स्वच्छाोत्सव', 'सत्यabama', 'सबarkanठा', 'कृष्णाiah', 'ട യ', 'डीिनो', 'Λ', 'д е л कabilan', 'ब्लूमबर्गNEF', 'कadinamकुलम', 'इंडियाAI', 'α', 'कadinamकुलम', 'न्यूार्क प्लसAI', '훼', 'खुुर्रम', 'उलेवााल 즐', 'शराा', 'किसangani', 'इम्युनोCAP', 'ப ம ர', '르 모', 'कृष्णाiah', 'α']
name gate entries: ["['Kochi = कोच्चि']", "['Lok Sabha = लोकसभा']", "['Parliament = संसद']", "['Tripura = त्रिपुरा']", "['Parliament = संसद']"]

=== body gate (85) ===
- 129967 [crime] 'Flydubai co-pilot attacked Indian pilot with crash axe during flight to Tel Aviv'
    error: body gate: {'gap_ok': False, 'gap': 'के को', 'number_ok': True, 'numbers_missing': [], 'numbers_extra': [], 'script_ok': True, 'bad_chars': '', 'entity_ok': True, 'entities_missing': []}
- 129934 [international] "Anthropic co-founder Christopher Olah faces dispute with Pope Leo XIV over Claude's moral "
    error: body gate: {'gap_ok': False, 'gap': 'के को', 'number_ok': True, 'numbers_missing': [], 'numbers_extra': [], 'script_ok': True, 'bad_chars': '', 'entity_ok': True, 'entities_missing': []}
- 129909 [economy] 'Kotak Multi Asset Allocation Fund beats Nifty 500 - TRI with 14.21% SIP return'
    error: body gate: {'gap_ok': True, 'gap': '', 'number_ok': True, 'numbers_missing': [], 'numbers_extra': ['2'], 'script_ok': False, 'bad_chars': 'ETब्यूरो', 'entity_ok': True, 'entities_missing': []}
- 129870 [economy] 'RK Fashion Accessories plans to raise Rs 35 crore through an NSE SME issue'
    error: body gate: {'gap_ok': True, 'gap': '', 'number_ok': True, 'numbers_missing': [], 'numbers_extra': [], 'script_ok': False, 'bad_chars': 'एवरेस्टIMS', 'entity_ok': True, 'entities_missing': []}
- 129866 [crime] 'Five workers died and 10 others were seriously injured'
    error: body gate: {'gap_ok': True, 'gap': '', 'number_ok': True, 'numbers_missing': [], 'numbers_extra': [], 'script_ok': False, 'bad_chars': 'मडििला', 'entity_ok': True, 'entities_missing': []}
- 129836 [regional] 'Despite repeated fires, Broadway in Kochi lacks proper firefighting infrastructure'
    error: body gate: {'gap_ok': False, 'gap': 'के के', 'number_ok': True, 'numbers_missing': [], 'numbers_extra': [], 'script_ok': True, 'bad_chars': '', 'entity_ok': True, 'entities_missing': []}

=== names (5) ===
- 127976 [other] 'Fire Engulfs 1808 Heritage Landmark Koder House in Fort Kochi'
    error: names: ['Kochi = कोच्चि']
- 127804 [politics] 'Rahul Gandhi Asserts Only Congress Can Defeat BJP and Modi'
    error: names: ['Lok Sabha = लोकसभा']
- 127260 [regional] 'Villupuram MP D. Ravikumar Reviews Central Schemes Implementation'
    error: names: ['Parliament = संसद']
- 118061 [politics] 'BJP, TIPRA Motha, IPFT Set for September Elections After Land Rights Deal'
    error: names: ['Tripura = त्रिपुरा']
- 117229 [politics] 'Dr Dharamvira Gandhi Missing from Punjab Electoral Rolls'
    error: names: ['Parliament = संसद']
