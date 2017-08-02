from Bio.Blast.Applications import NcbiblastnCommandline
from Bio import SeqIO
from Blastn import Blastn
from Bio.Seq import Seq
from Bio.Alphabet import generic_dna
from HSP import HSP
import os
import errno
import itertools
from itertools import permutations
from collections import defaultdict
import copy
from math import floor
import cProfile
import re

SCORE=135
E_VALUE_CUTOFF = 1.0
PERC_ID_CUTOFF = 80
QCOV_HSP_PERC = 80
WORD_SIZE = 7
MAX_MARGIN_BTWN_PRIMERS = 50 #<= #TODO: change this value to something smaller?
CUTOFF_GENE_LENGTH = 70
SNP_THRESHOLD = 4 #5 bp must be an exact match with 3' end of primer
MAX_MM_CONTIG_END = 10 # The amount of bp's that can be located on end/start of db primer before reaching the end/start of amp
MAX_MARGIN_AMP = 0
MIN_BSR = 0.6

def blastn_query(query_genes, database, qcov):
    """ Outputs stdout from blastn in xml format

    :param query_genes: A fasta file that contains the query genes that are being searched in the database
    :param database: A fasta file that contains the database that is being searched against
    :param out_file: A xml file that the blastn query
    :restrictions: Database is formatted using makeblastdb
    :return: None
    """
    #eval, perc iden, qcov
    if qcov == True:
        blastn_cline = NcbiblastnCommandline(query=query_genes, db=database, word_size=WORD_SIZE, outfmt=5,
                                             evalue=E_VALUE_CUTOFF, perc_identity=PERC_ID_CUTOFF, qcov_hsp_perc=QCOV_HSP_PERC)

    else:
        blastn_cline = NcbiblastnCommandline(query=query_genes, db=database, word_size=WORD_SIZE, outfmt=5,
                                             evalue=E_VALUE_CUTOFF, perc_identity=PERC_ID_CUTOFF)
    stdout, stderr = blastn_cline()
    return stdout


def bs_blast(query_genes, db):
    """ Outputs stdout from blastn in xml format using only % cutoff

    :param query_genes:
    :param db:
    :return:
    """
    blastn_cline = NcbiblastnCommandline(query=query_genes, db=db, word_size=WORD_SIZE, outfmt=5, perc_identity=PERC_ID_CUTOFF) #, task='blastn-short')
    stdout, stderr = blastn_cline()
    return stdout

def create_blastn_object(query_genes:str, database:str, qcov=False) -> Blastn:
    """ Return a blastn object with initialized blast_records and hsp_records using %iden, qcov, and evalue.

    :param query_genes: A fasta file that contains the query genes that are being searched in the database
    :param database: A fasta file that contains the database that is being searched against
    :param out_file: A string that contains the file location of the xml file being outputted
    :restrictions: Database is formatted using makeblastdb
    :return: Blastn object
    """
    blastn_object = Blastn()
    stdout_xml = blastn_query(query_genes, database, qcov)
    blastn_object.create_blast_records(stdout_xml)
    blastn_object.create_hsp_objects(query_genes)
    return blastn_object

def create_blastn_bsr_object(query_genes:str, db:str) -> Blastn:
    #TODO: merge with create_blastn_object
    """ Returns a blastn object with initialized blast record and hsp records using blast score ratio.

    :param query_genes: A path to a fasta file containing query genes
    :param db: A path to a fasta file containing db genes
    :return: Blastn
    """
    blastn_object = Blastn()
    stdout_xml = bs_blast(query_genes, db)
    blastn_object.create_blast_records(stdout_xml)
    blastn_object.create_hsp_objects(query_genes)
    return blastn_object

def valid_strands(first_hsp_object: HSP, second_hsp_object: HSP) -> None :
    """ Assigns valid attributes to first and second hsp object and modifies lo_first and lo_second hsp objects s.t. they only contain strands with the correct orientation to one another.

    :param first_hsp_object: A HSP object to compare with second_hsp_object
    :param second_hsp_object: A HSP object to compare with first_hsp_object
    :param lo_forward_hsp_objects: A list of hsp objects that was run using forward primer sets
    :param lo_reverse_hsp_objects: A list of hsp objects that was run using reverse primer sets
    :return: None
    """

    if first_hsp_object.name == second_hsp_object.name:
        if (first_hsp_object.strand or second_hsp_object.strand) and not (first_hsp_object.strand and second_hsp_object.strand):
            first_hsp_object.valid = True
            second_hsp_object.valid = True
        else:
            first_hsp_object.valid = False
            second_hsp_object.valid = False

def create_primer_dict(primers):
    """ Return a dictionary that contains primer sequences: where key is the primer id and value is the primer sequence.

    :param primers: A fasta file containing primer sequences
    :return: A dictionary
    """
    primer_dict = {}
    for primer in SeqIO.parse(primers, "fasta"):
        primer_dict[primer.id] = primer.seq
    return primer_dict

def is_distance(f_hsp_object, r_hsp_object, amplicon_sequences):
    """ Assigns f and r hsp_object's pcr_distance attrib. True if within MAX_MARGIN_BTWN_PRIMERS dist from each other.

    :param f_hsp_object: forward hsp object from blast primer search
    :param r_hsp_object: reverse hsp object from blastn primer search
    :param amplicon_sequences: amplicon sequences for reference of distance between f & r objects
    :return:
    """
    assert f_hsp_object.contig_name == r_hsp_object.contig_name
    distance = abs(f_hsp_object.start - r_hsp_object.start) + 1
    for amplicon in SeqIO.parse(amplicon_sequences, "fasta"):
        if f_hsp_object.name in amplicon.id:
            amplicon_length = len(amplicon.seq)
            f_hsp_object.pcr_distance = (abs(amplicon_length - distance) <= MAX_MARGIN_BTWN_PRIMERS)
            r_hsp_object.pcr_distance = (abs(amplicon_length - distance) <= MAX_MARGIN_BTWN_PRIMERS)



def pcr_directly(forward_hsp_object:HSP, reverse_hsp_object:HSP, amplicon_sequences:str, f_primers:dict, r_primers:dict):
    #TODO: refactor to remove f_primers and r_primers dict.
    """ Simulates epcr, checking if f and r objects are facing each other and the correct distance apart.

    :param forward_hsp_object: forward HSP object
    :param reverse_hsp_object: reverse HSP object
    :param amplicon_sequences: Path to fasta file containing amplicon sequences
    :param f_primers: Dictionary of f_primers
    :param r_primers: Dictionary containing r_primers
    :return:
    """

    valid_strands(forward_hsp_object, reverse_hsp_object)
    is_snp_primer_search(forward_hsp_object, f_primers, r_primers)
    is_snp_primer_search(reverse_hsp_object, f_primers, r_primers)
    is_distance(forward_hsp_object, reverse_hsp_object, amplicon_sequences)

    assert forward_hsp_object.valid == reverse_hsp_object.valid
    if forward_hsp_object.valid == True:
        if (forward_hsp_object.snp == False or reverse_hsp_object.snp == False):
            assert forward_hsp_object.pcr_distance == reverse_hsp_object.pcr_distance
            forward_hsp_object.epcr = reverse_hsp_object.pcr_distance
            reverse_hsp_object.epcr = reverse_hsp_object.pcr_distance
        else:
            forward_hsp_object.epcr, reverse_hsp_object.epcr = False, False
    else:
        forward_hsp_object.epcr, reverse_hsp_object.epcr = False, False


def is_snp_primer_search(hsp_object, f_primers, r_primers):
    """ Assigns snp attributes to hsp_object

    :param hsp_object: HSP object to check for 3' SNP
    :param f_primers: Dictionary of f_primers
    :param r_primers: Dictionary containing r_primers
    :return:
    """

    if hsp_object.name in f_primers and hsp_object.name in r_primers:
        f_primer = f_primers[hsp_object.name]
        r_primer = r_primers[hsp_object.name]
        forward_primer_seq = Seq(str(f_primer[len(f_primer) - SNP_THRESHOLD : len(f_primer)]), generic_dna)
        forward_primer_reverse_complement = forward_primer_seq.reverse_complement()
        reverse_primer_seq = Seq(str(r_primer[len(r_primer) - SNP_THRESHOLD : len(r_primer)]), generic_dna)
        reverse_primer_reverse_complement = reverse_primer_seq.reverse_complement()
        db_end_forward = hsp_object.sbjct[hsp_object.query_end - SNP_THRESHOLD : ]
        db_end_reverse = hsp_object.sbjct[hsp_object.query_end : hsp_object.query_end + SNP_THRESHOLD]

        # print('forward primer', forward_primer_seq)
        # print('forward primer rcomp', forward_primer_reverse_complement)
        # print('reverse primer', reverse_primer_seq)
        # print('reverse priemr rcomp', reverse_primer_reverse_complement)
        # print('db end forward', db_end_forward)
        # print('db end reverse', db_end_reverse)

        if str(forward_primer_seq) in db_end_forward\
                or str(forward_primer_reverse_complement) in db_end_forward\
                or str(reverse_primer_seq) in db_end_forward\
                or str(reverse_primer_reverse_complement) in db_end_forward:
            hsp_object.snp = False
        elif str(forward_primer_seq) in db_end_reverse\
                or str(forward_primer_reverse_complement) in db_end_reverse\
                or str(reverse_primer_seq) in db_end_reverse\
                or str(reverse_primer_reverse_complement) in db_end_reverse:
            hsp_object.snp = False
        else:
            hsp_object.snp = True

def valid_dir(hsp_obj:HSP):
    """ Assigns attr to hsp object: facing the end of the contig and within MAX_MM_CONTIG_END bp from end of contig.

    :param hsp_obj:
    :return:
    """
    if not (abs(hsp_obj.end - len(hsp_obj.sbjct)) <= MAX_MM_CONTIG_END and hsp_obj.start <= MAX_MM_CONTIG_END):
        if hsp_obj.strand and abs(hsp_obj.end - len(hsp_obj.sbjct)) <= MAX_MM_CONTIG_END:
            hsp_obj.location = True
            hsp_obj.end_dist = abs(hsp_obj.end - len(hsp_obj.sbjct))
        elif hsp_obj.strand == False and abs(hsp_obj.end) <= MAX_MM_CONTIG_END:
            hsp_obj.location = True
            hsp_obj.end_dist = abs(hsp_obj.end)
        elif hsp_obj.strand:
            hsp_obj.location = False
            hsp_obj.end_dist = abs(hsp_obj.end - len(hsp_obj.sbjct))
        elif not hsp_obj.strand:
            hsp_obj.location = False
            hsp_obj.end_dist = abs(hsp_obj.end)
    else:
        print('seq found over entire amp region!')
        hsp_obj.location = True
        hsp_obj.end_dist = abs(hsp_obj.end - len(hsp_obj.sbjct))

def ehybridization(blast_object:Blastn):
    """Assigns ehybrid attribute to all hsp objects in blast_objects

    :param blast_object:
    :return:
    """
    lo_ehybrid_hsp = [hsp for hsp in blast_object.hsp_objects if hsp.length >= CUTOFF_GENE_LENGTH]
    lo_failures = [hsp for hsp in blast_object.hsp_objects if hsp.length < CUTOFF_GENE_LENGTH]

    # print(len(blast_object.hsp_objects))
    # print(len(lo_ehybrid_hsp))
    # print(len(lo_failures))
    # print('lo failures', lo_failures)

    for hsp in lo_ehybrid_hsp:
        hsp.ehybrid = True
    for hsp in lo_failures:
        hsp.ehybrid = False
    lo_ehybrid_results = lo_ehybrid_hsp + lo_failures
    return lo_ehybrid_results

def false_neg_pred(attr_bin_tree:list, hsp:HSP, i:int) -> int:
    """ Using a prediction tree, determines the location of the hsp failure.

    :param attr_bin_tree: A list sorted as a binary tree
    :param hsp: A hsp object
    :param i: Location in tree
    :return: i (recursive fcn)
    """
    attr_i = attr_bin_tree[i]
    if attr_i == None:
        assert i != None
        return i
    elif getattr(hsp, attr_i) == None:
        return i
    elif getattr(hsp, attr_i):
        return false_neg_pred(attr_bin_tree, hsp, (2 * i) + 1)
    elif not getattr(hsp, attr_i):
        return false_neg_pred(attr_bin_tree, hsp, (2 * i) + 2)

def max_bs(file_dir:str) -> dict:
    """ Determines the maximum bits of all files in file_dir.

    :param file_dir: A path to a file dir containing fasta files for BSR reference
    :return: A dict containing the max bits of each file
    """

    dict_bits = {}
    files = (file for file in os.listdir(file_dir) if file.endswith('.fasta'))
    files_paths = []
    for file in files:
        files_paths.append(os.path.abspath(file_dir) + '/' + file)
    for file_path in files_paths:
            stdout_xml = bs_blast(file_path, file_path)
            blastn_obj = Blastn()
            blastn_obj.create_blast_records(stdout_xml)
            blastn_obj.create_hsp_objects(file_path)
            for hsp in blastn_obj.hsp_objects:
                dict_bits[hsp.name] = hsp.bits
    return dict_bits

def bsr(blast_object: Blastn, max_bits_dict: dict):
    """ Determines if the blast object's hsp objects has a BSR >= MIN_BSR

    :param blast_object: Blast object
    :param max_bits_dict: A dictionary containing the max bits, to compare with Blastn
    :return:
    """
    for hsp in blast_object.hsp_objects:
        if hsp.bits / max_bits_dict[hsp.name] < MIN_BSR:
            # print(hsp.bits / max_bits_dict[hsp.name])
            blast_object.hsp_objects.remove(hsp)

def same_contig_pred(lo_tup_same_contig, full_blast_qcov, dict_f_primers, dict_r_primers, max_f_bits_dict, max_r_bits_dict):
    """Predicts CGF when primers on same contig

    :param lo_tup_same_contig:
    :param full_blast_qcov:
    :param dict_f_primers:
    :param dict_r_primers:
    :param max_f_bits_dict:
    :param max_r_bits_dict:
    :return:
    """

    lo_hsp_ehybrid_qcov = ehybridization(full_blast_qcov) # assigns ehybrid attributes to each hsp from amp vs db
    ehybrid_qcov_pass = [hsp for hsp in lo_hsp_ehybrid_qcov if hsp.ehybrid == True]
    ehybrid_qcov_fail = [hsp for hsp in lo_hsp_ehybrid_qcov if hsp.ehybrid == False]

    result_dict = defaultdict(list)
    all_hsp = defaultdict(list)
    epcr_only_dict = defaultdict(list)

    for tup in lo_tup_same_contig:
        f_hsp = tup[0]
        r_hsp = tup[1]
        f_hsp.partner = tup[1]
        r_hsp.partner = tup[0]
        # f_hsp = copy.deepcopy(f_hsp_old)
        # r_hsp = copy.deepcopy(r_hsp_old)

        f_hsp.both_primers_found = True
        r_hsp.both_primers_found = True
        f_hsp.contig = True
        r_hsp.contig = True
        f_hsp.bsr = f_hsp.bits / max_f_bits_dict[f_hsp.name]
        r_hsp.bsr = r_hsp.bits / max_r_bits_dict[r_hsp.name]

        pcr_directly(f_hsp, r_hsp, amplicon_sequences, dict_f_primers, dict_r_primers) #assigns valid and snp attributes and pcr_distance and epcr
        assert f_hsp.epcr == r_hsp.epcr
        # if f_hsp.epcr == True and r_hsp.epcr == True:
        #     lo_tup_same_contig_pcr_results.append(tup)

        for hsp in ehybrid_qcov_pass:
            if f_hsp.start == hsp.start or r_hsp.start == hsp.start or f_hsp.end == hsp.end or r_hsp.end == hsp.end and f_hsp.name == hsp.name:
                # f_hsp.amp_found, r_hsp.amp_found = True, True
                f_hsp.ehybrid, r_hsp_ehybrid = True, True
                f_hsp.amp_len, r_hsp.amp_len = hsp.length, hsp.length
                f_hsp.amp_sbjct, r_hsp.amp_sbjct = hsp.sbjct, hsp.sbjct
                f_hsp.amp_query, r_hsp.amp_query = hsp.query, hsp.query
        for hsp in ehybrid_qcov_fail:
            if f_hsp.start == hsp.start or r_hsp.start == hsp.start or f_hsp.end == hsp.end or r_hsp.end == hsp.end and f_hsp.contig_name == hsp.contig_name:
                # f_hsp.amp_found, r_hsp.amp_found = True, True
                f_hsp.ehybrid, r_hsp.ehybrid = False, False
                f_hsp.amp_len, r_hsp.amp_len = hsp.length, hsp.length
                f_hsp.amp_sbjct = hsp.sbjct
                r_hsp.amp_sbjct = hsp.sbjct
                f_hsp.amp_query = hsp.query
                r_hsp.amp_query = hsp.query
        # assert f_hsp.ehybrid == r_hsp.ehybrid

        if (f_hsp.ehybrid or r_hsp.ehybrid) and f_hsp.epcr:
            # assert result_dict[f_hsp.name] == None
            f_hsp_new = copy.deepcopy(f_hsp)
            r_hsp_new = copy.deepcopy(r_hsp)
            result_dict[f_hsp.name].append(f_hsp_new)
            result_dict[r_hsp.name].append(r_hsp_new)
            all_hsp[f_hsp.name].append(f_hsp_new)
            all_hsp[r_hsp.name].append(r_hsp_new)
            # assert len(result_dict[f_hsp.name]) == 2
        if f_hsp.epcr and not (f_hsp.ehybrid or r_hsp.ehybrid):
            f_hsp_new = copy.deepcopy(f_hsp)
            r_hsp_new = copy.deepcopy(r_hsp)
            epcr_only_dict[f_hsp.name].append(f_hsp_new)
            epcr_only_dict[r_hsp.name].append(r_hsp_new)
            all_hsp[f_hsp.name].append(f_hsp_new)
            all_hsp[r_hsp.name].append(r_hsp_new)
        else:
            f_hsp_new = copy.deepcopy(f_hsp)
            r_hsp_new = copy.deepcopy(r_hsp)
            all_hsp[f_hsp.name].append(f_hsp_new)
            all_hsp[r_hsp.name].append(r_hsp_new)


    return [result_dict, all_hsp, epcr_only_dict]

def diff_contig_pred(lo_tup_diff_contig, max_f_bits_dict, max_r_bits_dict, ehybrid_pass, ehybrid_fail):
    """ Predicts CGF when primers on different contigs.

    :param lo_tup_diff_contig:
    :param max_f_bits_dict:
    :param max_r_bits_dict:
    :param ehybrid_pass:
    :param ehybrid_fail:
    :return:
    """
    result_dict = defaultdict(list)
    all_hsp = defaultdict(list)

    for tup in lo_tup_diff_contig:
        f_hsp = tup[0]
        r_hsp = tup[1]
        f_hsp.partner = tup[1]
        r_hsp.partner = tup[0]

        f_hsp.both_primers_found = True
        r_hsp.both_primers_found = True
        f_hsp.contig = False
        r_hsp.contig = False
        f_hsp.bsr = f_hsp.bits / max_f_bits_dict[f_hsp.name]
        r_hsp.bsr = r_hsp.bits / max_r_bits_dict[r_hsp.name]

        #todo: make into helper fcn
        for hsp in ehybrid_pass:
            if f_hsp.start == hsp.start or f_hsp.end == hsp.end and f_hsp.contig_name == hsp.contig_name:
                # f_hsp.amp_found = True
                f_hsp.ehybrid = True
                f_hsp.amp_len = hsp.length
                valid_dir(f_hsp)
                f_hsp.amp_sbjct = hsp.sbjct
                f_hsp.amp_query = hsp.query
            if r_hsp.start == hsp.start or r_hsp.end == hsp.end and r_hsp.contig_name == hsp.contig_name:
                # r_hsp.amp_found = True
                r_hsp.ehybrid = True
                r_hsp.amp_len = hsp.length
                valid_dir(r_hsp)
                r_hsp.amp_sbjct = hsp.sbjct
                r_hsp.amp_query = hsp.query
            # assert len(result_dict[f_hsp.name]) == 0
            if (f_hsp.ehybrid and r_hsp.ehybrid) and (f_hsp.location and r_hsp.location):
                f_hsp_new = copy.deepcopy(f_hsp)
                r_hsp_new = copy.deepcopy(r_hsp)
                result_dict[f_hsp.name].append((f_hsp_new, r_hsp_new))
                all_hsp[f_hsp.name].append(f_hsp_new)
                all_hsp[r_hsp.name].append(r_hsp_new)
            elif f_hsp.ehybrid and f_hsp.location:
                f_hsp_new = copy.deepcopy(f_hsp)
                result_dict[f_hsp.name].append((f_hsp_new, None))
                all_hsp[f_hsp.name].append(f_hsp_new)
            elif r_hsp.ehybrid and r_hsp.location:
                r_hsp_new = copy.deepcopy(r_hsp)
                result_dict[r_hsp.name].append((None, r_hsp_new))
                all_hsp[r_hsp.name].append(r_hsp_new)
            # assert len(result_dict[f_hsp.name]) < 3
        for hsp in ehybrid_fail:
            if f_hsp.start == hsp.start or f_hsp.end == hsp.end and f_hsp.contig_name == hsp.contig_name:
                # f_hsp.amp_found = True
                f_hsp.ehybrid = False
                f_hsp.amp_len = hsp.length
                f_hsp.amp_query = hsp.query
                f_hsp.amp_sbjct = hsp.sbjct

                f_hsp_new = copy.deepcopy(f_hsp)
                all_hsp[f_hsp.name].append(f_hsp_new)
            if r_hsp.start == hsp.start or r_hsp.end == hsp.end and r_hsp.contig_name == hsp.contig_name:
                # r_hsp.amp_found = True
                r_hsp.ehybrid = False
                r_hsp.amp_len = hsp.length
                r_hsp.amp_sbjct = hsp.sbjct
                r_hsp.amp_query = hsp.query

                r_hsp_new = copy.deepcopy(r_hsp)
                all_hsp[r_hsp.name].append(r_hsp_new)

    return[result_dict, all_hsp]

def one_primer_pred(lo_hsp_single_primers, ehybrid_pass, ehybrid_fail):
    """ Predicts CGF when only one primer is found (no partner primer)

    :param lo_hsp_single_primers:
    :param ehybrid_pass:
    :param ehybrid_fail:
    :return:
    """
    result_dict = defaultdict(list)
    all_hsp = defaultdict(list)
    for single_hsp in lo_hsp_single_primers:

        single_hsp.both_primers_found = False
        single_hsp.contig = False

        for blast_hsp in ehybrid_pass:
            if single_hsp.start == blast_hsp.start or single_hsp.end == blast_hsp.end and single_hsp.contig_name == blast_hsp.contig_name:
                # f_hsp.amp_found = True
                single_hsp.ehybrid = True
                single_hsp.amp_len = blast_hsp.length
                valid_dir(single_hsp)
                if single_hsp.location == True:
                    assert result_dict[single_hsp.name] == None
                    result_dict[single_hsp.name].append(single_hsp)
                    assert len(result_dict[single_hsp.name]) < 2
                single_hsp.amp_sbjct = blast_hsp.sbjct
                single_hsp.amp_query = blast_hsp.query
        for blast_hsp in ehybrid_fail:
            if single_hsp.start == blast_hsp.start or single_hsp.end == blast_hsp.end and single_hsp.contig_name == blast_hsp.contig_name:
                # f_hsp.amp_found = True
                single_hsp.ehybrid = False
                single_hsp.amp_len = blast_hsp.length
                single_hsp.amp_sbjct = blast_hsp.sbjct
                single_hsp.amp_query = blast_hsp.query

        all_hsp[single_hsp.name].append(single_hsp)
    return [result_dict, all_hsp]

def cgf_prediction_trial(forward_primers:str, reverse_primers:str, database:str, amp_sequences:str, max_f_bits_dict:dict, max_r_bits_dict, max_amp_bits_dict) -> list:
    """

    :param forward_primers:
    :param reverse_primers:
    :param database:
    :param amp_sequences:
    :param max_f_bits_dict:
    :param max_r_bits_dict:
    :param max_amp_bits_dict:
    :return:
    """

    # forward_blast = create_blastn_object(forward_primers, database, forward_out_file, True)
    # reverse_blast = create_blastn_object(reverse_primers, database, reverse_out_file, True)
    forward_blast_bsr = create_blastn_bsr_object(forward_primers, database)
    reverse_blast_bsr = create_blastn_bsr_object(reverse_primers, database)
    bsr(forward_blast_bsr, max_f_bits_dict)
    bsr(reverse_blast_bsr, max_r_bits_dict)
    blast_object = create_blastn_object(amp_sequences, database)
    full_blast_qcov = create_blastn_object(amp_sequences, database, True)
    dict_f_primers = create_primer_dict(forward_primers)
    dict_r_primers = create_primer_dict(reverse_primers)

    # lo_tup_same_queries = match_primer_queries(forward_blast_bsr.hsp_objects, reverse_blast_bsr.hsp_objects)
    lo_tup_same_queries = [(f_hsp,r_hsp) for f_hsp in forward_blast_bsr.hsp_objects for r_hsp in reverse_blast_bsr.hsp_objects if f_hsp.name == r_hsp.name]
    print('lo_tup_same_queries')
    lo_tup_same_contig = [tup for tup in lo_tup_same_queries if tup[0].contig_name == tup[1].contig_name]

    #same contig prediction
    same_contig_pred_results = same_contig_pred(lo_tup_same_contig, full_blast_qcov, dict_f_primers, dict_r_primers, max_f_bits_dict, max_r_bits_dict)
    result_dict_same_contig = same_contig_pred_results[0]
    all_hsp_same_contig = same_contig_pred_results[1]
    print('same contig results', result_dict_same_contig)

    #Does not look for genes after they were found on the same contig
    # lo_tup_diff_contig = [tup for tup in lo_tup_same_queries if tup not in lo_tup_same_contig]
    lo_tup_diff_contig = [tup for tup in lo_tup_same_queries if tup not in lo_tup_same_contig]
    result_keys = [key for key,value in result_dict_same_contig.items()]
    lo_tup_diff_contig_not_found = [tup for tup in lo_tup_diff_contig if tup[0].name not in result_keys]

    try:
        lo_f_primers, lo_r_primers = zip(*lo_tup_same_queries)
    except ValueError:
        lo_f_primers = []
        lo_r_primers = []

    f_hsp_single_primers = [hsp for hsp in forward_blast_bsr.hsp_objects if hsp not in lo_f_primers]
    r_hsp_single_primers = [hsp for hsp in reverse_blast_bsr.hsp_objects if hsp not in lo_r_primers]

    for f_hsp in f_hsp_single_primers:
        f_hsp.bsr = f_hsp.bits / max_f_bits_dict[f_hsp.name]
    for r_hsp in r_hsp_single_primers:
        r_hsp.bsr = r_hsp.bits / max_r_bits_dict[r_hsp.name]
    lo_hsp_single_primers = list(itertools.chain(f_hsp_single_primers, r_hsp_single_primers))

    result_dict = defaultdict(list)
    all_hsp = defaultdict(list)

    lo_hsp_ehybrid = ehybridization(blast_object)  # assigns ehybrid attributes to each hsp from amp vs db
    ehybrid_hsp_pass = [hsp for hsp in lo_hsp_ehybrid if hsp.ehybrid == True]
    ehybrid_hsp_fail = [hsp for hsp in lo_hsp_ehybrid if hsp.ehybrid == False]
    for hsp in ehybrid_hsp_fail:
        if hsp.length >= CUTOFF_GENE_LENGTH:
            print('length cutoff', hsp.length)

    diff_contig_pred_results = diff_contig_pred(lo_tup_diff_contig_not_found, max_f_bits_dict, max_r_bits_dict, ehybrid_hsp_pass, ehybrid_hsp_fail)
    result_dict_diff_contig = diff_contig_pred_results[0]
    all_hsp_diff_contig = diff_contig_pred_results[1]
    print('diff contig results found')

    #One primer found prediction
    one_primer_results = one_primer_pred(lo_hsp_single_primers, ehybrid_hsp_pass, ehybrid_hsp_fail)
    result_dict_one_primer = one_primer_results[0]
    all_hsp_one_primer = one_primer_results[1]

    all_hsp.update(all_hsp_same_contig)
    all_hsp.update(all_hsp_diff_contig)
    all_hsp.update(all_hsp_one_primer)
    result_dict.update(result_dict_same_contig)
    result_dict.update(result_dict_diff_contig)
    result_dict.update(result_dict_one_primer)

    results_list = [result_dict, all_hsp]

    return results_list

def main(db_dir, f_prims, r_prims, amp_seqs):
    """

    :param db_directory: The location of the directory with fasta database files contained (already formatted using makeblastdb)
    :param forward_primers: The fasta file location of the forward primers
    :param reverse_primers: The fasta file location of the reverse primers
    :param amplicon_sequences: The fasta file location of the amplicon sequences
    :return: A dictionary
    """

    # bsr
    f_bs_primer_dir = "/home/sfisher/Sequences/BSR/f_primers/"
    r_bs_primer_dir = "/home/sfisher/Sequences/BSR/r_primers/"
    amp_bs_dir = "/home/sfisher/Sequences/BSR/amp_seq/"
    max_f_bits_dict = max_bs(f_bs_primer_dir)
    max_r_bits_dict = max_bs(r_bs_primer_dir)
    max_amp_bits_dict = max_bs(amp_bs_dir)

    files = (file for file in os.listdir(test_11168_cases) if file.endswith('.fasta'))
    files_paths = []
    for file in files:
        files_paths.append(os.path.abspath(test_11168_cases) + '/' + file)

    cgf_predictions_dict = {}
    for file_path in files_paths:
        file_name = file_path.partition(db_dir + "/")[2]
        result = cgf_prediction_trial(f_prims, r_prims, file_path, amp_seqs, max_f_bits_dict,
                                      max_r_bits_dict, max_amp_bits_dict)[0]
        cgf_predictions_dict[file_name] = result
    return cgf_predictions_dict



forward_primers = "/home/sfisher/Sequences/cgf_forward_primers.fasta" #fasta file with primer id's and primer sequences
reverse_primers = "/home/sfisher/Sequences/cgf_reverse_primers.fasta" #fasta file with primer id's and primer sequences
amplicon_sequences = "/home/sfisher/Sequences/amplicon_sequences/amplicon_sequences.fasta"
test_pcr_prediction = "/home/sfisher/Sequences/amplicon_sequences/individual_amp_seq/test_pcr_prediction"
test_bp_removed = "/home/sfisher/Sequences/amplicon_sequences/individual_amp_seq/test_bp_removed"
test_gene_annotation = "/home/sfisher/Sequences/amplicon_sequences/individual_amp_seq/test_gene_annotation_error"
test_contig_trunc = "/home/sfisher/Sequences/amplicon_sequences/individual_amp_seq/test_contig_trunc"
test_valid_dir = "/home/sfisher/Sequences/amplicon_sequences/individual_amp_seq/test_valid_dir"
test_11168 = "/home/sfisher/Sequences/11168_complete_genome"
test_11168_cases = "/home/sfisher/Sequences/11168_test_files/gnomes_for_shannah"
test_cprofile = "/home/sfisher/Sequences/11168_test_files/memory_trial"
test_cprofile_file = "/home/sfisher/Sequences/11168_test_files/memory_trial/06_2855.fasta"

if __name__ == "__main__":
    # cProfile.run('main(test_11168_cases, forward_primers, reverse_primers, amplicon_sequences); print')
    main(test_11168_cases, forward_primers, reverse_primers, amplicon_sequences)


# TODO: check for error for if makeblastdb hasn't been run and return an error message to the user indicating the error.


