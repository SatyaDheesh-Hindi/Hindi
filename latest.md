given up: 19
ids: 129866,129776,129234,129075,128860,128596,128472,128363,127804,127698,127625,127254,127202,126623,126050,122903,122195,120979,120837
by reason: [('body gate', 18), ('names', 1)]
by month scraped: [('2026-09', 14), ('2026-10', 5)]
by category: [('international', 5), ('crime', 5), ('politics', 4), ('regional', 3), ('economy', 1), ('environment', 1)]
by source: [('The Hindu', 11), ('Times of India', 3), ('Al Jazeera', 2), ('NDTV', 2), ('Economic Times', 1)]
failing checks: [(('script_ok',), 14), (('number_ok',), 4), (('names',), 1)]
extra numbers in Hindi: [('5', 1), ('3', 1), ('10', 1), ('4', 1), ('2', 1), ('8', 1), ('9', 1)]
missing numbers: [('1', 3), ('2', 1), ('10000', 1), ('15000', 1), ('2030', 1), ('2031', 1), ('21', 1), ('351', 1), ('57000', 1)]
bad script samples: ['मडििला', 'सत्यabama', 'सबarkanठा', 'ട യ', 'д е л कabilan', 'कadinamकुलम', 'कadinamकुलम', 'न्यूार्क', '훼', 'खुुर्रम', 'उलेवााल 즐', 'किसangani', '르 모', 'कृष्णाiah']
name gate entries: ["['Lok Sabha = लोकसभा']"]

=== body gate (18) ===
- 129866 [crime] 'Five workers died and 10 others were seriously injured'
    error: body gate: {'gap_ok': True, 'gap': '', 'number_ok': True, 'numbers_missing': [], 'numbers_extra': [], 'script_ok': False, 'bad_chars': 'मडििला', 'entity_ok': True, 'entities_missing': []}
- 129776 [politics] 'Dharapuram byelection: 2.23 lakh electors vote as residents pay ₹5,000 for water'
    error: body gate: {'gap_ok': True, 'gap': '', 'number_ok': True, 'numbers_missing': [], 'numbers_extra': [], 'script_ok': False, 'bad_chars': 'सत्यabama', 'entity_ok': True, 'entities_missing': []}
- 129234 [crime] 'Police seize Rs 1,72,80,000 worth of illegal cannabis in Kotda Gadhi village'
    error: body gate: {'gap_ok': True, 'gap': '', 'number_ok': True, 'numbers_missing': [], 'numbers_extra': [], 'script_ok': False, 'bad_chars': 'सबarkanठा', 'entity_ok': True, 'entities_missing': []}
- 129075 [environment] 'M. Suchitra wins G. Prabhakaran-OISCA Environmental Excellence Award for 35 years of journ'
    error: body gate: {'gap_ok': True, 'gap': '', 'number_ok': True, 'numbers_missing': [], 'numbers_extra': [], 'script_ok': False, 'bad_chars': 'ട യ', 'entity_ok': True, 'entities_missing': []}
- 128860 [international] 'S. Jaishankar meets JVP delegation to strengthen BJP-JVP engagement and deepen ties'
    error: body gate: {'gap_ok': True, 'gap': '', 'number_ok': True, 'numbers_missing': [], 'numbers_extra': ['5'], 'script_ok': False, 'bad_chars': 'д е л कabilan', 'entity_ok': True, 'entities_missing': []}
- 128596 [politics] 'Fadnavis promises judicial inquiry, MPSC Secretary transfer as aspirants suspend October a'
    error: body gate: {'gap_ok': True, 'gap': '', 'number_ok': False, 'numbers_missing': ['2'], 'numbers_extra': ['3'], 'script_ok': True, 'bad_chars': '', 'entity_ok': True, 'entities_missing': []}

=== names (1) ===
- 127804 [politics] 'Rahul Gandhi Asserts Only Congress Can Defeat BJP and Modi'
    error: names: ['Lok Sabha = लोकसभा']
