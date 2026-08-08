    import chromadb
    from langchain_community.document_loaders import TextLoader
    from langchain_text_splitters import RecursiveCharacterTextSplitter
    from langchain_huggingface import HuggingFaceEmbeddings

    # using the phase 3 lecanemab clinical trial abstract so I have real data to test
    sample_pubmed_abstract = """
    Background: The accumulation of soluble and insoluble aggregated amyloid-beta (Aβ) may initiate or potentiate pathologic processes in Alzheimer's disease. Lecanemab, a humanized IgG1 monoclonal antibody that binds with high affinity to Aβ soluble protofibrils, is being tested in persons with early Alzheimer's disease.
    Methods: We conducted an 18-month, multicenter, double-blind, phase 3 trial involving persons 50 to 90 years of age with early Alzheimer's disease (mild cognitive impairment or mild dementia due to Alzheimer's disease) with evidence of amyloid on positron-emission tomography (PET) or by cerebrospinal fluid testing. Participants were randomly assigned in a 1:1 ratio to receive intravenous lecanemab (10 mg per kilogram of body weight every 2 weeks) or placebo. The primary end point was the change from baseline at 18 months in the score on the Clinical Dementia Rating-Sum of Boxes (CDR-SB; range, 0 to 18, with higher scores indicating greater impairment). Key secondary end points were the change in amyloid burden on PET, the score on the 14-item cognitive subscale of the Alzheimer's Disease Assessment Scale (ADAS-cog14; range, 0 to 90; higher scores indicate greater impairment), the Alzheimer's Disease Composite Score (ADCOMS; range, 0 to 1.97; higher scores indicate greater impairment), and the score on the Alzheimer's Disease Cooperative Study-Activities of Daily Living Scale for Mild Cognitive Impairment (ADCS-MCI-ADL; range, 0 to 53; lower scores indicate greater impairment).
    Results: A total of 1795 participants were enrolled, with 898 assigned to receive lecanemab and 897 to receive placebo. The mean CDR-SB score at baseline was approximately 3.2 in both groups. The adjusted least-squares mean change from baseline at 18 months was 1.21 with lecanemab and 1.66 with placebo (difference, -0.45; 95% confidence interval [CI], -0.67 to -0.23; P<0.001). In a substudy involving 698 participants, there were greater reductions in brain amyloid burden with lecanemab than with placebo (difference, -59.1 centiloids; 95% CI, -62.6 to -55.6). Other mean differences between the two groups in the change from baseline favoring lecanemab were as follows: for the ADAS-cog14 score, -1.44 (95% CI, -2.27 to -0.61; P<0.001); for the ADCOMS, -0.050 (95% CI, -0.074 to -0.027; P<0.001); and for the ADCS-MCI-ADL score, 2.0 (95% CI, 1.2 to 2.8; P<0.001). Lecanemab resulted in infusion-related reactions in 26.4% of the participants and amyloid-related imaging abnormalities with edema or effusions in 12.6%.
    Conclusions: Lecanemab reduced markers of amyloid in early Alzheimer's disease and resulted in moderately less decline on measures of cognition and function than placebo at 18 months but was associated with adverse events. Longer trials are warranted to determine the efficacy and safety of lecanemab in early Alzheimer's disease. (Funded by Eisai and Biogen; Clarity AD ClinicalTrials.gov number, NCT03887455.).
    """

    print("splitting text...")
    # keeping chunks small. need overlap so words don't get cut in half
    text_splitter = RecursiveCharacterTextSplitter(chunk_size=150, chunk_overlap=30, length_function=len)
    chunks = text_splitter.create_documents([sample_pubmed_abstract])

    # using local hf model so I don't burn openAI credits while testing
    print("loading embedding model...")
    local_embeddings = HuggingFaceEmbeddings(model_name="all-MiniLM-L6-v2")

    # setup local db folder
    client = chromadb.PersistentClient(path="./chroma_local_storage")
    collection = client.get_or_create_collection(name="pubmed_test")

    # get text out of the document objects
    chunk_texts = [c.page_content for c in chunks]
    chunk_ids = [f"id_{i}" for i in range(len(chunks))]

    print("saving to chromadb...")
    embedded_vectors = local_embeddings.embed_documents(chunk_texts)
    collection.add(documents=chunk_texts, embeddings=embedded_vectors, ids=chunk_ids)

    print("done. testing search...")
    query = "Did lecanemab reduce brain amyloid burden?"
    query_vector = local_embeddings.embed_query(query)

    results = collection.query(query_embeddings=[query_vector], n_results=2)
    print("results:", results['documents'][0])