"""
Run this once after creating the database to seed AEFUNAi data:
    python seed_data.py
"""
from app import app
from models import db, Faculty, Department, Course, Admin
from flask_bcrypt import Bcrypt

bcrypt = Bcrypt(app)

AEFUNAI_DATA = {
    "Faculty of Physical Sciences": {
        "Computer Science/Informatics": {
            100: {
                1: [
                    ("CSC101", "Introduction to Computer Science"),
                    ("MTH101", "Mathematics I"),
                    ("PHY101", "General Physics I"),
                    ("CHM101", "General Chemistry I"),
                    ("GST101", "Use of English I"),
                ],
                2: [
                    ("CSC102", "Introduction to Programming"),
                    ("MTH102", "Mathematics II"),
                    ("PHY102", "General Physics II"),
                    ("GST102", "Use of English II"),
                    ("CSC104", "Computer Applications"),
                ]
            },
            200: {
                1: [
                    ("CSC201", "Data Structures"),
                    ("CSC203", "Object-Oriented Programming"),
                    ("CSC205", "Discrete Mathematics"),
                    ("MTH201", "Mathematical Methods I"),
                    ("GST201", "Nigerian Peoples and Culture"),
                ],
                2: [
                    ("CSC202", "Computer Organization"),
                    ("CSC204", "Logic Design"),
                    ("CSC206", "Database Systems I"),
                    ("MTH202", "Mathematical Methods II"),
                    ("GST202", "Entrepreneurship"),
                ]
            },
            300: {
                1: [
                    ("CSC301", "Operating Systems"),
                    ("CSC303", "Algorithm Analysis"),
                    ("CSC305", "Software Engineering"),
                    ("CSC307", "Computer Networks"),
                    ("CSC309", "Database Systems II"),
                ],
                2: [
                    ("CSC302", "Compiler Design"),
                    ("CSC304", "Artificial Intelligence"),
                    ("CSC306", "Systems Analysis & Design"),
                    ("CSC308", "Human-Computer Interaction"),
                    ("CSC310", "Web Technologies"),
                ]
            },
            400: {
                1: [
                    ("CSC401", "Machine Learning"),
                    ("CSC403", "Computer Security"),
                    ("CSC405", "Mobile Computing"),
                    ("CSC407", "Cloud Computing"),
                    ("CSC409", "Research Methods"),
                ],
                2: [
                    ("CSC402", "Deep Learning"),
                    ("CSC404", "Distributed Systems"),
                    ("CSC406", "Computer Vision"),
                    ("CSC408", "Internet of Things"),
                    ("CSC410", "Final Year Project"),
                ]
            },
        },
        "Mathematics": {
            100: {
                1: [("MTH101", "Algebra I"), ("MTH103", "Trigonometry"), ("GST101", "Use of English I")],
                2: [("MTH102", "Calculus I"), ("MTH104", "Geometry"), ("GST102", "Use of English II")]
            },
            200: {
                1: [("MTH201", "Real Analysis I"), ("MTH203", "Linear Algebra"), ("MTH205", "Statistics I")],
                2: [("MTH202", "Real Analysis II"), ("MTH204", "Abstract Algebra"), ("MTH206", "Statistics II")]
            },
            300: {
                1: [("MTH301", "Complex Analysis"), ("MTH303", "Numerical Methods"), ("MTH305", "Topology")],
                2: [("MTH302", "Differential Equations"), ("MTH304", "Mathematical Modelling"), ("MTH306", "Operations Research")]
            },
            400: {
                1: [("MTH401", "Functional Analysis"), ("MTH403", "Research Methods"), ("MTH405", "Graph Theory")],
                2: [("MTH402", "Final Year Project"), ("MTH404", "Mathematical Economics"), ("MTH406", "Stochastic Processes")]
            },
        },
        "Physics": {
            100: {
                1: [("PHY101", "Mechanics"), ("PHY103", "Heat & Thermodynamics"), ("GST101", "Use of English I")],
                2: [("PHY102", "Electricity & Magnetism"), ("PHY104", "Waves & Optics"), ("GST102", "Use of English II")]
            },
            200: {
                1: [("PHY201", "Classical Mechanics"), ("PHY203", "Modern Physics"), ("PHY205", "Electronics I")],
                2: [("PHY202", "Quantum Mechanics I"), ("PHY204", "Electromagnetism"), ("PHY206", "Electronics II")]
            },
            300: {
                1: [("PHY301", "Quantum Mechanics II"), ("PHY303", "Statistical Mechanics"), ("PHY305", "Nuclear Physics")],
                2: [("PHY302", "Solid State Physics"), ("PHY304", "Atomic Physics"), ("PHY306", "Computational Physics")]
            },
            400: {
                1: [("PHY401", "Laser Physics"), ("PHY403", "Astrophysics"), ("PHY405", "Research Methods")],
                2: [("PHY402", "Final Year Project"), ("PHY404", "Medical Physics"), ("PHY406", "Environmental Physics")]
            },
        },
    },
    "Faculty of Engineering": {
        "Electrical/Electronic Engineering": {
            100: {
                1: [("EEE101", "Engineering Mathematics I"), ("EEE103", "Basic Electrical"), ("GST101", "Use of English I")],
                2: [("EEE102", "Engineering Mathematics II"), ("EEE104", "Circuit Theory"), ("GST102", "Use of English II")]
            },
            200: {
                1: [("EEE201", "Electronics I"), ("EEE203", "Signals & Systems"), ("EEE205", "Electromagnetic Fields")],
                2: [("EEE202", "Electronics II"), ("EEE204", "Control Systems I"), ("EEE206", "Digital Electronics")]
            },
            300: {
                1: [("EEE301", "Power Systems"), ("EEE303", "Microprocessors"), ("EEE305", "Communication Systems")],
                2: [("EEE302", "Power Electronics"), ("EEE304", "Control Systems II"), ("EEE306", "Instrumentation")]
            },
            400: {
                1: [("EEE401", "Renewable Energy"), ("EEE403", "Embedded Systems"), ("EEE405", "Research Methods")],
                2: [("EEE402", "Final Year Project"), ("EEE404", "Telecommunications"), ("EEE406", "Robotics")]
            },
        },
        "Civil Engineering": {
            100: {
                1: [("CVE101", "Engineering Drawing"), ("CVE103", "Engineering Mathematics I"), ("GST101", "Use of English I")],
                2: [("CVE102", "Mechanics of Materials"), ("CVE104", "Engineering Mathematics II"), ("GST102", "Use of English II")]
            },
            200: {
                1: [("CVE201", "Structural Analysis I"), ("CVE203", "Fluid Mechanics I"), ("CVE205", "Surveying I")],
                2: [("CVE202", "Structural Analysis II"), ("CVE204", "Fluid Mechanics II"), ("CVE206", "Surveying II")]
            },
            300: {
                1: [("CVE301", "Soil Mechanics"), ("CVE303", "Reinforced Concrete"), ("CVE305", "Highway Engineering")],
                2: [("CVE302", "Foundation Engineering"), ("CVE304", "Steel Structures"), ("CVE306", "Water Resources")]
            },
            400: {
                1: [("CVE401", "Construction Management"), ("CVE403", "Environmental Engineering"), ("CVE405", "Research Methods")],
                2: [("CVE402", "Final Year Project"), ("CVE404", "Traffic Engineering"), ("CVE406", "Bridge Engineering")]
            },
        },
    },
    "Faculty of Biological Sciences": {
        "Biology": {
            100: {
                1: [("BIO101", "General Biology I"), ("CHM101", "General Chemistry I"), ("GST101", "Use of English I")],
                2: [("BIO102", "General Biology II"), ("CHM102", "General Chemistry II"), ("GST102", "Use of English II")]
            },
            200: {
                1: [("BIO201", "Cell Biology"), ("BIO203", "Genetics I"), ("BIO205", "Microbiology I")],
                2: [("BIO202", "Plant Physiology"), ("BIO204", "Genetics II"), ("BIO206", "Microbiology II")]
            },
            300: {
                1: [("BIO301", "Ecology"), ("BIO303", "Molecular Biology"), ("BIO305", "Immunology")],
                2: [("BIO302", "Evolution"), ("BIO304", "Biochemistry"), ("BIO306", "Parasitology")]
            },
            400: {
                1: [("BIO401", "Bioinformatics"), ("BIO403", "Research Methods"), ("BIO405", "Toxicology")],
                2: [("BIO402", "Final Year Project"), ("BIO404", "Conservation Biology"), ("BIO406", "Marine Biology")]
            },
        },
    },
    "Faculty of Social Sciences": {
        "Economics": {
            100: {
                1: [("ECO101", "Principles of Economics I"), ("MTH101", "Mathematics I"), ("GST101", "Use of English I")],
                2: [("ECO102", "Principles of Economics II"), ("MTH102", "Mathematics II"), ("GST102", "Use of English II")]
            },
            200: {
                1: [("ECO201", "Microeconomics I"), ("ECO203", "Statistics for Economists"), ("ECO205", "Economic History")],
                2: [("ECO202", "Macroeconomics I"), ("ECO204", "Mathematics for Economists"), ("ECO206", "Development Economics")]
            },
            300: {
                1: [("ECO301", "Microeconomics II"), ("ECO303", "Econometrics I"), ("ECO305", "Public Finance")],
                2: [("ECO302", "Macroeconomics II"), ("ECO304", "Econometrics II"), ("ECO306", "International Economics")]
            },
            400: {
                1: [("ECO401", "Financial Economics"), ("ECO403", "Research Methods"), ("ECO405", "Labour Economics")],
                2: [("ECO402", "Final Year Project"), ("ECO404", "Agricultural Economics"), ("ECO406", "Industrial Economics")]
            },
        },
    },
}

def seed():
    with app.app_context():
        # Only seed if faculties table is empty
        if Faculty.query.count() > 0:
            from app import logger
            logger.info("Data already seeded. Skipping.")
            return

        from app import logger
        logger.info("Seeding AEFUNAi faculties, departments and courses...")

        for faculty_name, departments in AEFUNAI_DATA.items():
            faculty = Faculty(name=faculty_name)
            db.session.add(faculty)
            db.session.flush()

            for dept_name, levels in departments.items():
                dept = Department(name=dept_name, faculty_id=faculty.id)
                db.session.add(dept)
                db.session.flush()

                for level, semesters in levels.items():
                    for semester, courses in semesters.items():
                        for code, title in courses:
                            course = Course(
                                code=code,
                                title=title,
                                level=level,
                                semester=semester,
                                department_id=dept.id
                            )
                            db.session.add(course)

        # Create default admin
        if Admin.query.count() == 0:
            admin = Admin(
                username='admin',
                email='admin@aefunai.edu.ng',
                password=bcrypt.generate_password_hash('Admin@1234').decode('utf-8')
            )
            db.session.add(admin)
            logger.info("Default admin created: username=admin, password=Admin@1234")

        db.session.commit()
        logger.info("✅ Seeding complete!")
        logger.info(f"   Faculties: {Faculty.query.count()}")
        logger.info(f"   Departments: {Department.query.count()}")
        logger.info(f"   Courses: {Course.query.count()}")

if __name__ == '__main__':
    seed()
