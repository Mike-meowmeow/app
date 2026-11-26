"""Starting list of licenses (PRC) and eligibilities (CSC) for the reference
list (core.models.Credential), loaded by `manage.py seed_reference_data`.

Drafted on 27 November 2026 from general knowledge of the PRC's regulated
professions and the CSC's eligibilities, not from a live official source. HR
or the System Administrator should check it against the official lists and
correct it on the Licenses and eligibilities page. Seeding only adds missing
names; it never changes or removes an entry someone edited.

Each entry: (name, [other spellings]). Abbreviations that mean something else
in a school (for example "ME", "CE") are left out of the other spellings.
"""

LICENSES = [
    ('Professional Teacher', ['Licensed Professional Teacher', 'LPT', 'LET',
                              'LET passer', 'Licensure Examination for Teachers']),
    ('Certified Public Accountant', ['CPA']),
    ('Registered Nurse', ['RN', 'Nurse']),
    ('Physician', ['Licensed Physician', 'Medical Doctor']),
    ('Dentist', []),
    ('Pharmacist', []),
    ('Medical Technologist', ['Registered Medical Technologist', 'RMT']),
    ('Midwife', ['Registered Midwife']),
    ('Nutritionist-Dietitian', ['Registered Nutritionist-Dietitian', 'RND']),
    ('Physical Therapist', ['Registered Physical Therapist', 'PTRP']),
    ('Occupational Therapist', ['Registered Occupational Therapist']),
    ('Optometrist', []),
    ('Radiologic Technologist', ['Registered Radiologic Technologist', 'RRT']),
    ('X-Ray Technologist', []),
    ('Respiratory Therapist', []),
    ('Veterinarian', ['Doctor of Veterinary Medicine license']),
    ('Psychologist', ['Registered Psychologist']),
    ('Psychometrician', ['Registered Psychometrician', 'RPm']),
    ('Guidance Counselor', ['Registered Guidance Counselor', 'RGC']),
    ('Social Worker', ['Registered Social Worker', 'RSW']),
    ('Librarian', ['Registered Librarian']),
    ('Criminologist', ['Registered Criminologist']),
    ('Civil Engineer', ['Registered Civil Engineer']),
    ('Mechanical Engineer', ['Registered Mechanical Engineer', 'PME', 'RME']),
    ('Certified Plant Mechanic', []),
    ('Electrical Engineer', ['Registered Electrical Engineer', 'REE']),
    ('Master Electrician', ['Registered Master Electrician', 'RME (Master Electrician)']),
    ('Electronics Engineer', ['ECE', 'Electronics and Communications Engineer']),
    ('Electronics Technician', ['ECT']),
    ('Chemical Engineer', ['Registered Chemical Engineer']),
    ('Chemist', ['Registered Chemist']),
    ('Chemical Technician', []),
    ('Geodetic Engineer', []),
    ('Geologist', []),
    ('Mining Engineer', []),
    ('Metallurgical Engineer', []),
    ('Sanitary Engineer', []),
    ('Agricultural and Biosystems Engineer', ['Agricultural Engineer']),
    ('Aeronautical Engineer', []),
    ('Naval Architect and Marine Engineer', ['Naval Architect']),
    ('Architect', ['Registered Architect']),
    ('Interior Designer', []),
    ('Landscape Architect', []),
    ('Environmental Planner', []),
    ('Master Plumber', []),
    ('Agriculturist', ['Licensed Agriculturist']),
    ('Fisheries Professional', ['Fisheries Technologist']),
    ('Forester', []),
    ('Food Technologist', []),
    ('Customs Broker', []),
    ('Real Estate Broker', []),
    ('Real Estate Appraiser', []),
    ('Real Estate Consultant', []),
    ('Marine Deck Officer', []),
    ('Marine Engineer Officer', []),
]

ELIGIBILITIES = [
    ('Career Service Professional', ['CS Professional', 'CSE Professional',
                                     'Career Service Professional Eligibility',
                                     'Career Service Eligibility - Professional']),
    ('Career Service Sub-Professional', ['CS Sub-Professional', 'CSE Sub-Professional',
                                         'Career Service Subprofessional',
                                         'Career Service Sub-Professional Eligibility']),
    ('RA 1080 (Board/Bar)', ['RA 1080', 'Republic Act 1080', 'Board passer', 'Bar passer',
                             'Board eligibility', 'Bar eligibility']),
    ('PD 907 (Honor Graduate)', ['PD 907', 'Honor Graduate Eligibility',
                                 'Honor graduate']),
    ('Foreign School Honor Graduate Eligibility', ['FSHGE']),
    ('Scientific and Technological Specialist Eligibility', ['PD 997', 'STSE']),
    ('Electronic Data Processing Specialist Eligibility', ['EDPSE']),
    ('Sanggunian Member Eligibility', ['RA 10156']),
    ('Barangay Official Eligibility', ['BOE']),
    ('Barangay Health Worker Eligibility', ['BHWE']),
    ('Barangay Nutrition Scholar Eligibility', ['BNSE']),
    ('Skills Eligibility - Category II', ['Skills Eligibility']),
]
