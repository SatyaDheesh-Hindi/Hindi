given up: 247
by reason: [('body gate', 213), ('names', 34)]
by month scraped: [('2026-07', 64), ('2026-09', 165), ('2026-10', 18)]
by category: [('international', 78), ('other', 45), ('economy', 38), ('politics', 25), ('crime', 17), ('regional', 11), ('education', 10), ('environment', 9)]
by source: [('The Hindu', 52), ('Times of India', 50), ('NDTV', 49), ('Al Jazeera', 32), ('Indian Express', 19), ('BBC', 16), ('Economic Times', 15), ('Wired', 9)]
failing checks: [(('script_ok',), 89), (('gap_ok',), 80), (('number_ok',), 40), (('names',), 34), (('script_ok', 'gap_ok'), 3), (('gap_ok', 'number_ok'), 1)]
extra numbers in Hindi: [('6', 11), ('000', 9), ('3', 7), ('5', 6), ('8', 6), ('10', 4), ('4', 4), ('250', 3), ('2', 3), ('7', 3)]
missing numbers: [('1', 8), ('7', 5), ('2', 4), ('250000', 3), ('27', 1), ('500', 1), ('130', 1), ('175000', 1), ('450000', 1), ('132', 1)]
bad script samples: ['सबarkanथा', 'कृष्णाiah', 'ട യ', 'डीिनो', 'Λ', 'कabilan', 'ब्लूमबर्गNEF', 'कadinamकुलम', 'इंडियाAI', 'α', 'कadinamकुलम', 'न्यूार्क प्लसAI', 'दिल्ली-NCR', 'AI-आधारित', 'गैर-SC/ST', '훼', 'AI-पावर्ड', 'खुुर्रम', 'फोर्ज-BEML-डेटा', 'AITC-ममता—को गुटों—AITC-डेमोक्रेटिक', 'SMS-ओनली', 'भिलवारियाFinserv', 'सतुवाचारी-VIT', 'US-मेक्सिको', 'उलेवााल 즐', 'LNG-पावर्ड डीजल-प्लस-LNG', 'IIT-बॉम्बे', 'C-पोस्ट', 'लिवestream', 'प्री-IPO', 'US-ईरान', 'US—के', 'AI-जनरेटेड', 'US-ईरान', 'NYC-डेमोक्रेटिक', 'AI-इनेबल्ड', 'चीन-US', 'RT-आधारित', 'AI-पावर्ड', 'चीन-US']
name gate entries: ["['Houthi = हौथी']", "['Kochi = कोच्चि']", "['Lok Sabha = लोकसभा']", "['Parliament = संसद']", "['Union Council of Ministers = यूनियन काउंसिल ऑफ मिनिस्टर्स']", "['Juhu = जुहू']", "['United Nations = संयुक्त राष्ट्र']", "['Karma-Dharma = कर्मा-धर्म']", "['Islamabad = इस्लामाबाद']", "['New Yorker = न्यूयॉर्कर']", "['Pennsylvania = पेंसिल्वेनिया']", "['Iranian = ईरानी']", "['Iranian = ईरानी']", "['Iranian officials = ईरानी अधिकारी']", "['British Parliament = ब्रिटिश पार्लियामेंट']", "['European Parliament = यूरोपियन पार्लियामेंट']", "['The United Nations = द यूनाइटेड नेशंस']", "['Gaza = गाज़ा']", "['Florida = फ्लोरिडा']", "['Tripura = त्रिपुरा']", "['Palestinians = फलस्तीनी']", "['Parliament = संसद']", "['Parliament = संसद']", "['Rajasthan = राजस्थान']", "['Bengaluru = बेंगलुरु']", "['India = भारत']", "['Karnataka = कर्नाटक']", "['Parliament = संसद']", "['London = लंदन']", "['Maharashtra = महाराष्ट्र']", "['Parliament = संसद']", "['Fars = फार्स']", "['Parliament = संसद']", "['Parliament = संसद']"]

=== body gate (213) ===
- 129234 [crime] 'Police seize Rs 1,72,80,000 worth of illegal cannabis in Kotda Gadhi village'
    error: body gate: {'gap_ok': True, 'gap': '', 'number_ok': True, 'numbers_missing': [], 'numbers_extra': [], 'script_ok': False, 'bad_chars': 'सबarkanथा', 'entity_ok': True, 'entities_missing': []}
- 129142 [environment] 'APPCB Chairman orders GRI Towers and Sentini Pipes to develop greenbelts in SPSR Nellore'
    error: body gate: {'gap_ok': True, 'gap': '', 'number_ok': True, 'numbers_missing': [], 'numbers_extra': ['5'], 'script_ok': False, 'bad_chars': 'कृष्णाiah', 'entity_ok': True, 'entities_missing': []}
- 129129 [crime] '19-year-old BCom student Yogesh stabbed to death near Mahesh PU College in K.K. Layout'
    error: body gate: {'gap_ok': False, 'gap': 'को के', 'number_ok': True, 'numbers_missing': [], 'numbers_extra': ['5'], 'script_ok': True, 'bad_chars': '', 'entity_ok': True, 'entities_missing': []}
- 129112 [international] 'Indian pilot Smit Machchhar praised by Netanyahu for unlocking cockpit door during axe att'
    error: body gate: {'gap_ok': False, 'gap': 'ने को', 'number_ok': True, 'numbers_missing': [], 'numbers_extra': [], 'script_ok': True, 'bad_chars': '', 'entity_ok': True, 'entities_missing': []}
- 129111 [international] 'David Sheegog and wife transform Anaheim backyard into miniature Disney world over 27 year'
    error: body gate: {'gap_ok': True, 'gap': '', 'number_ok': False, 'numbers_missing': ['27'], 'numbers_extra': [], 'script_ok': True, 'bad_chars': '', 'entity_ok': True, 'entities_missing': []}
- 129075 [environment] 'M. Suchitra wins G. Prabhakaran-OISCA Environmental Excellence Award for 35 years of journ'
    error: body gate: {'gap_ok': True, 'gap': '', 'number_ok': True, 'numbers_missing': [], 'numbers_extra': [], 'script_ok': False, 'bad_chars': 'ട യ', 'entity_ok': True, 'entities_missing': []}

=== names (34) ===
- 128082 [international] "Pakistan's Defense Minister Vows to Defend Saudi Arabia from Iran-Backed Houthi Rebels"
    error: names: ['Houthi = हौथी']
- 127976 [other] 'Fire Engulfs 1808 Heritage Landmark Koder House in Fort Kochi'
    error: names: ['Kochi = कोच्चि']
- 127804 [politics] 'Rahul Gandhi Asserts Only Congress Can Defeat BJP and Modi'
    error: names: ['Lok Sabha = लोकसभा']
- 127260 [regional] 'Villupuram MP D. Ravikumar Reviews Central Schemes Implementation'
    error: names: ['Parliament = संसद']
- 124853 [politics] "PM Modi To Chair 2-Day 'Chintan Shivir' With Cabinet Ministers From Tomorrow"
    error: names: ['Union Council of Ministers = यूनियन काउंसिल ऑफ मिनिस्टर्स']
- 124756 [economy] 'Embassy Developments rallies 8% after Rs 711 crore residential tower deal with Angel One f'
    error: names: ['Juhu = जुहू']
