given up: 69
by reason: [('body gate', 64), ('names', 5)]
by month scraped: [('2026-09', 49), ('2026-10', 20)]
by category: [('international', 21), ('politics', 14), ('economy', 9), ('crime', 8), ('other', 7), ('regional', 4), ('education', 2), ('environment', 2)]
by source: [('The Hindu', 26), ('Times of India', 14), ('NDTV', 13), ('Al Jazeera', 5), ('TechCrunch', 5), ('BBC', 4), ('Economic Times', 1), ('Wired', 1)]
failing checks: [(('gap_ok',), 42), (('script_ok',), 20), (('names',), 5), (('number_ok',), 2)]
extra numbers in Hindi: [('5', 2), ('10', 2), ('6', 2), ('7', 1), ('3', 1), ('2', 1), ('8', 1), ('9', 1)]
missing numbers: [('2', 1), ('1', 1)]
bad script samples: ['सबarkanठा', 'कृष्णाiah', 'ട യ', 'डीिनो', 'Λ', 'д е л कabilan', 'ब्लूमबर्गNEF', 'कadinamकुलम', 'इंडियाAI', 'α', 'कadinamकुलम', 'न्यूार्क प्लसAI', '훼', 'खुुर्रम', 'इंश्योरेंसDekho भिलवारियाFinserv', 'शराा', 'किसangani', '르 모', 'कृष्णाiah', 'त्सित्सipas']
name gate entries: ["['Kochi = कोच्चि']", "['Lok Sabha = लोकसभा']", "['Parliament = संसद']", "['Tripura = त्रिपुरा']", "['Parliament = संसद']"]

=== body gate (64) ===
- 129461 [politics] 'Vijay launches ‘Vettri Payanam’ offering no-fare travel for 84.32 lakh women and transgend'
    error: body gate: {'gap_ok': False, 'gap': 'को की', 'number_ok': True, 'numbers_missing': [], 'numbers_extra': ['7'], 'script_ok': True, 'bad_chars': '', 'entity_ok': True, 'entities_missing': []}
- 129415 [international] 'Israeli Minister praises Captain Smit Machchhar for subduing co-pilot attempting to crash '
    error: body gate: {'gap_ok': False, 'gap': 'के को', 'number_ok': True, 'numbers_missing': [], 'numbers_extra': [], 'script_ok': True, 'bad_chars': '', 'entity_ok': True, 'entities_missing': []}
- 129363 [economy] 'Sarvjeet Singh Virk, Co-founder & MD of Shoonya'
    error: body gate: {'gap_ok': False, 'gap': 'के को', 'number_ok': True, 'numbers_missing': [], 'numbers_extra': [], 'script_ok': True, 'bad_chars': '', 'entity_ok': True, 'entities_missing': []}
- 129362 [education] 'N.R. Narayana Murthy tells students honesty and integrity are essential for developed Indi'
    error: body gate: {'gap_ok': False, 'gap': 'के को', 'number_ok': True, 'numbers_missing': [], 'numbers_extra': [], 'script_ok': True, 'bad_chars': '', 'entity_ok': True, 'entities_missing': []}
- 129331 [economy] '87.7% of individual equity derivatives traders lost Rs 91,685 crore in FY26'
    error: body gate: {'gap_ok': False, 'gap': 'के को', 'number_ok': True, 'numbers_missing': [], 'numbers_extra': [], 'script_ok': True, 'bad_chars': '', 'entity_ok': True, 'entities_missing': []}
- 129298 [politics] 'Street battles erupt in Keralam as activists clash over Gyanesh Kumar’s exit'
    error: body gate: {'gap_ok': False, 'gap': 'को के', 'number_ok': True, 'numbers_missing': [], 'numbers_extra': [], 'script_ok': True, 'bad_chars': '', 'entity_ok': True, 'entities_missing': []}

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
