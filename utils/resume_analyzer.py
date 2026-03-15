import spacy
import re
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

# Load the NLP model once when the server starts
try:
    nlp = spacy.load("en_core_web_sm")
except OSError:
    print("Downloading English model for spaCy...")
    from spacy.cli import download
    download("en_core_web_sm")
    nlp = spacy.load("en_core_web_sm")

def analyze_resume_custom(resume_text, target_job_description=None):
    """
    Analyzes resume text against a target job description using NLP, ML, and Regex.
    Returns a dictionary matching the schema expected by the frontend.
    """
    if not target_job_description:
        # Default target if none is provided
        target_job_description = """
        Software Engineer required. Experience in Python, Flask, JavaScript, HTML, CSS.
        Strong understanding of Data Structures, Algorithms, SQL, databases, and Git.
        Machine Learning, NLP, Firebase, and TailwindCSS are a plus.
        """

    score = 0
    strengths = []
    weaknesses = []
    suggestions = []
    
    # --- PILLAR 1: Machine Learning (TF-IDF & Cosine Similarity) [Max 40 pts] ---
    try:
        vectorizer = TfidfVectorizer(stop_words='english')
        tfidf_matrix = vectorizer.fit_transform([resume_text, target_job_description])
        similarity = cosine_similarity(tfidf_matrix[0:1], tfidf_matrix[1:2])[0][0]
        
        ml_score = min(40, int(similarity * 100))
        score += ml_score
        
        if similarity > 0.25:
            strengths.append(f"Strong keyword match ({int(similarity*100)}%) with standard software engineering profiles.")
        else:
            weaknesses.append(f"Low keyword match ({int(similarity*100)}%). Resume lacks standard industry terms.")
            suggestions.append("Add missing technical keywords (e.g., Python, SQL, Git, frameworks) to match job descriptions.")
    except Exception as e:
        print(f"ML Processing error: {e}")

    # --- PILLAR 2: NLP (SpaCy Entity & Verb Analysis) [Max 30 pts] ---
    doc = nlp(resume_text)
    
    # Action Verbs (VBD = past tense, VBG = gerund)
    action_verbs = [token.text for token in doc if token.tag_ in ['VBD', 'VBG']]
    if len(action_verbs) > 5:
        score += 15
        strengths.append("Excellent use of action verbs to describe experience and projects.")
    else:
        score += 5
        weaknesses.append("Lacking strong action verbs.")
        suggestions.append("Start bullet points with verbs like 'Engineered', 'Designed', 'Developed', or 'Implemented'.")

    # Organizations & Education (ORG)
    orgs = [ent.text for ent in doc.ents if ent.label_ == 'ORG']
    if len(orgs) >= 2:
        score += 15
        strengths.append("Educational institutions and organizations are clearly formatted and recognized.")
    else:
        score += 5
        suggestions.append("Ensure your universities, past companies, or project names are clearly stated and capitalized.")

    # --- PILLAR 3: Rule-Based (Regex for formatting) [Max 30 pts] ---
    
    # Contact Info
    has_email = bool(re.search(r'[\w\.-]+@[\w\.-]+', resume_text))
    has_phone = bool(re.search(r'\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}', resume_text))
    
    if has_email and has_phone:
        score += 15
        strengths.append("Standard contact information is present.")
    else:
        weaknesses.append("Missing basic contact information.")
        suggestions.append("Add a clear email address and phone number at the top of your resume.")
        
    # Professional Links
    has_links = bool(re.search(r'linkedin\.com|github\.com', resume_text.lower()))
    if has_links:
        score += 15
        strengths.append("Professional portfolios (LinkedIn/GitHub) are linked.")
    else:
        suggestions.append("Include links to your LinkedIn profile or GitHub to showcase your work.")

    # --- Generate Final Output ---
    score = min(100, max(0, score)) # Cap between 0-100
    
    if score >= 80:
        summary = "Excellent resume! It is highly relevant, well-formatted, and ATS-friendly."
    elif score >= 60:
        summary = "Good resume, but missing a few key elements that could improve your ATS ranking."
    else:
        summary = "Needs improvement. Focus on adding relevant technical keywords and standardizing your format."

    return {
        "score": score,
        "summary": summary,
        "strengths": strengths,
        "weaknesses": weaknesses,
        "suggestions": suggestions
    }